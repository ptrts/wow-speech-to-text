cd yandex-cloud-cloudapi

"D:\Program Files\Python310\python.exe" -m venv .venv
.\.venv\Scripts\activate

pip install --upgrade pip
pip install grpcio-tools PyAudio

python -m grpc_tools.protoc -I cloudapi -I cloudapi/third_party/googleapis ^
   --python_out=. ^
   --grpc_python_out=. ^
     google/api/http.proto ^
     google/api/annotations.proto ^
     yandex/cloud/api/operation.proto ^
     google/rpc/status.proto ^
     yandex/cloud/operation/operation.proto ^
     yandex/cloud/validation.proto ^
     yandex/cloud/ai/stt/v3/stt_service.proto ^
     yandex/cloud/ai/stt/v3/stt.proto
