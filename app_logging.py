import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(filename)s/%(funcName)s:%(lineno)d: %(message)s",
    datefmt="%H:%M:%S",
)

TRACE = 5
logging.addLevelName(TRACE, "TRACE")
