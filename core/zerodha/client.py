import logging
import os
from time import sleep

from dotenv import load_dotenv
from kiteconnect import KiteConnect, exceptions
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from agents.common.utils import get_TOTP

# It's good practice to have this at the top of the main script or here
load_dotenv()


logger = logging.getLogger(__name__)


class ZerodhaClient:
    """
    A robust, singleton-pattern-like class to manage Zerodha KiteConnect sessions.
    Handles automated login, session validation, and provides a shared client instance.
    """

    _kite_instance = None
    _access_token_path = "data/zerodha_access_token.txt"  # Use a file to persist the token

    def __init__(self):
        # This init should do as little as possible.
        # The main logic is in the class method `get_instance`.
        pass

    @classmethod
    def _save_access_token(cls, token):
        logger.info("Saving new access token to file for persistence...")
        os.makedirs(os.path.dirname(cls._access_token_path), exist_ok=True)
        with open(cls._access_token_path, "w") as f:
            f.write(token)
        logger.info("Access token saved successfully.")

    @classmethod
    def _load_access_token(cls):
        if os.path.exists(cls._access_token_path):
            logger.info("Found persistent access token file. Loading token.")
            with open(cls._access_token_path, "r") as f:
                return f.read().strip()
        logger.info("No persistent access token file found.")
        return None

    @classmethod
    def _perform_automated_login(cls):
        """
        Handles the selenium-based login flow to generate a new access token.
        This is a private class method and should not be called directly.
        Returns the new access token on success, None on failure.
        """
        logger.info("Starting automated Zerodha login process...")
        try:
            api_key = os.getenv("ZERODHA_API_KEY")
            if not api_key:
                logger.critical("ZERODHA_API_KEY not found in environment variables.")
                return None

            kite = KiteConnect(api_key=api_key)
            login_url = kite.login_url()

            options = webdriver.ChromeOptions()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")  # Important for running in Docker/Linux
            driver = webdriver.Chrome(options=options)

            logger.info("Opening Zerodha login page in headless browser...")
            driver.get(login_url)

            # Use explicit waits and more reliable selectors (like 'id' or 'name' when available)
            wait = WebDriverWait(driver, 20)  # Wait up to 20 seconds

            # --- Login Page ---
            logger.info("Entering user credentials...")
            # Zerodha uses 'id' for these fields, which is much more stable than XPath
            wait.until(EC.presence_of_element_located((By.ID, "userid"))).send_keys(os.getenv("ZERODHA_USERNAME"))
            wait.until(EC.presence_of_element_located((By.ID, "password"))).send_keys(os.getenv("ZERODHA_PASSWORD"))
            wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()
            logger.info("Credentials submitted.")

            # --- Wait for OTP Page ---
            logger.info("Waiting for OTP page to load...")
            # We wait for the OTP input field to be present, which indicates the page has loaded
            sleep(2)  # Give some time for the page to load, can be adjusted based on network speed

            # --- TOTP Page ---
            logger.info("Entering TOTP...")
            totp = get_TOTP()
            if not totp:
                logger.error("Failed to generate TOTP. Aborting login.")
                driver.quit()
                return None

            # Zerodha uses 'id' for the TOTP field as well
            wait.until(EC.presence_of_element_located((By.ID, "userid"))).send_keys(str(totp))  # Zerodha reuses 'userid' ID for TOTP input
            # wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']"))).click()
            logger.info("TOTP submitted successfully.")

            # --- Wait for redirect and extract request_token ---
            logger.info("Waiting for redirect to get request_token...")
            # We wait until the URL contains 'request_token', which is a very reliable condition
            wait.until(EC.url_contains("request_token"))
            final_url = driver.current_url
            logger.info(f"Redirect successful. Final URL: {final_url}")
            driver.quit()

            request_token = final_url.split("request_token=")[1].split("&")[0]
            logger.info(f"Successfully extracted request_token: {request_token}")

            # --- Generate Session ---
            logger.info("Generating final session and access_token...")
            api_secret = os.getenv("ZERODHA_API_SECRET")
            session_data = kite.generate_session(request_token, api_secret=api_secret)
            access_token = session_data["access_token"]

            cls._save_access_token(access_token)

            return access_token

        except Exception as e:
            logger.exception(f"An error occurred during the automated login process: {e}")
            if "driver" in locals() and driver:
                driver.quit()
            return None

    @classmethod
    def get_instance(cls):
        """
        The main public method to get a valid KiteConnect client instance.
        Implements a singleton-like pattern to ensure only one client object.
        Validates the existing session and performs login if necessary.
        """
        if cls._kite_instance:
            logger.info("Existing Kite client instance found. Validating session...")
            try:
                # A lightweight call to check if the session is valid
                cls._kite_instance.profile()
                logger.info("Session is valid. Reusing existing client instance.")
                return cls._kite_instance
            except exceptions.TokenException:
                logger.warning("Session token has expired. Re-authentication required.")
                cls._kite_instance = None  # Invalidate the instance
            except Exception as e:
                logger.error(f"An unexpected error occurred while validating session: {e}. Re-authenticating.")
                cls._kite_instance = None

        # --- If instance is None (either first time or after expiry) ---
        logger.info("No valid client instance found. Attempting to initialize...")

        kite = KiteConnect(api_key=os.getenv("ZERODHA_API_KEY"))

        # Try to load token from file first
        access_token = cls._load_access_token()

        if access_token:
            logger.info("Attempting to set access token from persistent file.")
            try:
                kite.set_access_token(access_token)
                kite.profile()  # Validate it immediately
                logger.info("Successfully initialized client with persistent access token.")
                cls._kite_instance = kite
                return cls._kite_instance
            except exceptions.TokenException:
                logger.warning("Persistent access token is invalid or expired. Performing full login.")
            except Exception as e:
                logger.error(f"Error validating persistent token: {e}. Performing full login.")

        # If loading from file failed or no file existed, perform full login
        new_access_token = cls._perform_automated_login()

        if new_access_token:
            kite.set_access_token(new_access_token)
            cls._kite_instance = kite
            logger.info("New Zerodha client instance created and authenticated successfully.")
            return cls._kite_instance
        else:
            logger.critical("Failed to create and authenticate Zerodha client. The bot cannot proceed with broker operations.")
            return None
