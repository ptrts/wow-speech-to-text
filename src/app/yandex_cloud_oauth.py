from __future__ import annotations

from typing import TypedDict, Optional
import http.server
import socketserver
import threading
import urllib.parse
import time
import webbrowser
import secrets
import base64
import hashlib
from typing import Any, Dict
import requests

from app.app_logging import logging, TRACE


logger = logging.getLogger(__name__)

PORT = 52123
CALLBACK_PATH = "/oauth/callback"
REDIRECT_URI = f"http://localhost:{PORT}{CALLBACK_PATH}"

CLIENT_ID = "50988b5588004fc1bdc757217b681a9b"
SCOPE = "cloud:auth"

AUTH_URL = "https://oauth.yandex.ru/authorize"
TOKEN_URL = "https://oauth.yandex.com/token"
IAM_URL = "https://iam.api.cloud.yandex.net/iam/v1/tokens"


class OAuth:
    code: str
    code_verifier: str
    code_challenge: str

    def __init__(self):
        self.state = secrets.token_urlsafe(16)
        self.code_verifier, self.code_challenge = OAuth._generate_pkce_pair()

    def launch(self):
        auth_url = OAuth._build_auth_url(CLIENT_ID, REDIRECT_URI, SCOPE, self.state, self.code_challenge)

        logger.info("Открываю браузер по адресу:")
        logger.info(auth_url)
        webbrowser.open_new_tab(auth_url)

    @staticmethod
    def _generate_pkce_pair() -> tuple[str, str]:
        """
        Генерим (code_verifier, code_challenge) для PKCE (S256).
        """
        # verifier: произвольная строка 43–128 символов
        code_verifier = secrets.token_urlsafe(64)[:128]
        code_verifier_bytes = code_verifier.encode("ascii")

        digest = hashlib.sha256(code_verifier_bytes).digest()
        code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return code_verifier, code_challenge

    @staticmethod
    def _build_auth_url(
            client_id: str,
            redirect_uri: str,
            scope: str,
            state: str,
            code_challenge: str
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return AUTH_URL + "?" + urllib.parse.urlencode(params)


class OAuthResult(TypedDict):
    code: Optional[str]
    state: Optional[str]
    error: Optional[str]
    raw_query: dict[str, list[str]]


class OAuthTCPServer(socketserver.TCPServer):

    state: str
    auth_result: Optional[OAuthResult] = None

    def __init__(self, server_address, RequestHandlerClass, state: str, bind_and_activate=True):
        self.state = state
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        pass


class OAuthCallbackHandler(http.server.BaseHTTPRequestHandler):
    # Подсказываем типизатору, что server именно OAuthTCPServer
    server: OAuthTCPServer

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        if parsed.path != CALLBACK_PATH:
            self.send_error(404, "Not Found")
            return

        # Выводим в лог поступивший запрос
        logger.info("===== RAW HTTP REQUEST =====")
        logger.info(self.requestline)
        logger.info("")
        raw_headers = self.headers.as_bytes().decode("iso-8859-1", errors="replace")
        logger.info(raw_headers.rstrip("\r\n"))
        logger.info("")

        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        state = params.get("state", [None])[0]
        error = params.get("error", [None])[0]

        if state != self.server.state:
            self.send_error(400, "Bad State")
            return

        # Здесь типизатор уже знает, что у сервера есть auth_result
        self.server.auth_result = {
            "code": code,
            "state": state,
            "error": error,
            "raw_query": params,
        }

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        html = """
        <html>
          <body>
            <h1>Авторизация завершена</h1>
            <p>Это окно можно закрыть и вернуться в приложение.</p>
          </body>
        </html>
        """
        self.wfile.write(html.encode("utf-8"))

    def log_message(self, format, *args):
        # чтобы не спамило логами
        pass


def launch_server(state: str):
    httpd = OAuthTCPServer(("127.0.0.1", PORT), OAuthCallbackHandler, state)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def wait_for_oauth_callback(httpd: OAuthTCPServer, timeout: float):
    logger.info(f"Жду редиректа на http://127.0.0.1:%s%s ...", PORT, CALLBACK_PATH)

    started = time.time()
    while True:
        if time.time() - started > timeout:
            threading.Thread(target=httpd.shutdown, daemon=True).start()
            raise TimeoutError("Не дождались OAuth-редиректа от Яндекса")

        auth_result = httpd.auth_result
        if auth_result is None:
            time.sleep(0.1)
            continue

        threading.Thread(target=httpd.shutdown, daemon=True).start()
        return auth_result


def get_oauth_and_iam_tokens(timeout: float = 300.0) -> Dict[str, Any]:

    oauth = OAuth()

    with launch_server(oauth.state) as httpd:
        oauth.launch()
        cb = wait_for_oauth_callback(httpd, timeout)

    logger.info("Callback: %s", cb)
    # cb ожидается вида:
    # {
    #   "code": str | None,
    #   "state": str | None,
    #   "error": str | None,
    #   "raw_query": dict[str, list[str]]
    # }

    if cb.get("error"):
        raise RuntimeError(f"OAuth error: {cb['error']}")

    if cb.get("state") != oauth.state:
        raise RuntimeError(f"state mismatch: ожидали {oauth.state}, получили {cb.get('state')}")

    code = cb.get("code")
    if not code:
        raise RuntimeError("В callback нет параметра 'code'")

    logger.info("Обмениваю code на OAuth-токен...")
    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": oauth.code_verifier,
        # device_id / device_name можно добавить при желании
    }
    resp = requests.post(
        TOKEN_URL,
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    resp.raise_for_status()
    tj = resp.json()
    # По доке:
    # {
    #   "token_type": "bearer",
    #   "access_token": "...",
    #   "expires_in": 1234,
    #   "refresh_token": "...",
    #   "scope": "..."
    # }
    oauth_access_token = tj["access_token"]
    oauth_refresh_token = tj.get("refresh_token")
    oauth_expires_in = tj.get("expires_in")

    logger.info("OAuth-токен получен.")

    logger.info("Обмениваю OAuth-токен на IAM-токен...")
    iam_resp = requests.post(
        IAM_URL,
        json={"yandexPassportOauthToken": oauth_access_token},
        timeout=10,
    )
    iam_resp.raise_for_status()
    ij = iam_resp.json()
    # По доке:
    # { "iamToken": "...", "expiresAt": "..." }
    iam_token = ij["iamToken"]
    iam_expires_at = ij.get("expiresAt")

    logger.info("IAM-токен получен.")

    return {
        "oauth_access_token": oauth_access_token,
        "oauth_refresh_token": oauth_refresh_token,
        "oauth_expires_in": oauth_expires_in,
        "iam_token": iam_token,
        "iam_expires_at": iam_expires_at,
    }
