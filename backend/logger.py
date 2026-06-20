import logging
import sys

# Configure the logging format with timestamps
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ],
    force=True  # Override default logging configurations
)

# Export the application logger
logger = logging.getLogger("SpotiAI")
