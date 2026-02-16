import time
import base64
import re
import json
import os
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException, NoSuchWindowException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# Constants
COMPANY_RADIO_BUTTON_XPATH = "//input[@type='radio' and @title='Company']"
USERNAME_INPUT_ID = "username"
PASSWORD_INPUT_XPATH = "//input[@name='password']"
MOBILE_NUMBER_INPUT_XPATH = "//input[@placeholder='Enter Your Phone Number']"
SEND_OTP_BUTTON_XPATH = "//button[@name='sendotp']"
OTP_INPUT_XPATH = "//input[@placeholder='Enter Your OTP']"
INCORRECT_OTP_XPATH = "//*[contains(text(), 'Incorrect OTP')]"
INCORRECT_PASSWORD_XPATH = "//*[contains(text(), 'incorrect password')]"
CANDIDATE_COUNT_LINKS_XPATH = "//div[@class='main_right_heading']/h2/div/a"
RESULTS_TABLE_ID = "tfhover"
RANK_FILTER_ID = "rank"
SHIP_TYPE_FILTER_ID = "ship"
FILTER_SUBMIT_BUTTON_ID = "submit"
CANDIDATE_LINK_IN_TABLE_XPATH = ".//a[contains(@href, 'view_cand_details.php')]"
DOWNLOAD_PDF_BUTTON_XPATH = "//a[contains(@href, 'download.php') or contains(text(), 'DOWNLOAD PDF')]"
DOWNLOAD_PAGE_CONTENT_VERIFICATION_XPATH = "//*[self::th or self::td][contains(text(), 'Name')]"
NEXT_PAGE_BUTTON_XPATH = "//a[contains(., 'Next')]"
DASHBOARD_URL = "http://seajob.net/company/dashboard.php" # URL to navigate back to


class Scraper:
    def __init__(self, download_folder):
        self.driver = None
        self.wait = None
        self.base_download_folder = download_folder

    def _setup_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument("start-maximized")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_argument('--headless')
        service = Service(ChromeDriverManager().install())
        self.driver = webdriver.Chrome(service=service, options=options)
        self.wait = WebDriverWait(self.driver, 30)

    def start_session(self, username, password, mobile_number):
        self._setup_driver()
        self.driver.get("http://seajob.net/seajob_login.php")
        self.wait.until(EC.element_to_be_clickable((By.XPATH, COMPANY_RADIO_BUTTON_XPATH))).click()
        self.driver.find_element(By.ID, USERNAME_INPUT_ID).send_keys(username)
        self.driver.find_element(By.XPATH, PASSWORD_INPUT_XPATH).send_keys(password)
        self.driver.find_element(By.XPATH, MOBILE_NUMBER_INPUT_XPATH).send_keys(mobile_number)
        self.driver.find_element(By.XPATH, SEND_OTP_BUTTON_XPATH).click()
        try:
            WebDriverWait(self.driver, 10).until(EC.alert_is_present())
            alert = self.driver.switch_to.alert
            alert_text = alert.text
            alert.accept()
            if "Not Registered" in alert_text:
                self.quit()
                return {"success": False, "message": f"Login failed: {alert_text}"}
            return {"success": True, "message": "OTP Sent."}
        except TimeoutException:
            self.quit()
            return {"success": False, "message": "No confirmation alert appeared."}

    def verify_otp(self, otp_code):
        try:
            self.driver.find_element(By.XPATH, OTP_INPUT_XPATH).send_keys(otp_code + Keys.RETURN)
            long_wait = WebDriverWait(self.driver, 20)
            long_wait.until(EC.element_to_be_clickable((By.XPATH, CANDIDATE_COUNT_LINKS_XPATH)))
            return {"success": True}
        except Exception as e:
            try:
                self.driver.find_element(By.XPATH, INCORRECT_PASSWORD_XPATH)
                return {"success": False, "message": "Incorrect password entered."}
            except NoSuchElementException:
                try:
                    self.driver.find_element(By.XPATH, INCORRECT_OTP_XPATH)
                    return {"success": False, "message": "Incorrect OTP entered."}
                except NoSuchElementException:
                    return {"success": False, "message": f"Login failed (unknown reason): {str(e)}"}

    def _save_page_as_pdf(self, folder, filename):
        os.makedirs(folder, exist_ok=True)
        file_path = os.path.join(folder, filename)
        try:
            result = self.driver.execute_cdp_cmd("Page.printToPDF", {"printBackground": True})
            with open(file_path, "wb") as f: f.write(base64.b64decode(result['data']))
            return True
        except Exception: return False

    def _process_single_list(self, logger, rank, ship_type, target_folder, existing_ids):
        page_number = 1
        while True:
            logger.info(f"--- Processing page {page_number} ---")
            
            try:
                current_table = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.ID, RESULTS_TABLE_ID))
                )
            except TimeoutException:
                if "No candidate found" in self.driver.page_source or "No records found" in self.driver.page_source:
                    logger.warning("No candidates found for this filter combination.")
                    return
                else:
                    logger.error("Timed out waiting for results table.")
                    raise

            urls = [link.get_attribute('href') for link in self.driver.find_elements(By.XPATH, CANDIDATE_LINK_IN_TABLE_XPATH)]
            logger.info(f"Found {len(urls)} candidates on this page.")
            if not urls: break
            
            for url in urls:
                main_window = self.driver.current_window_handle
                id_match = re.search(r"cand_id=([^&]*)", url)
                if not id_match: continue
                raw_id = id_match.group(1)
                candidate_id = base64.b64decode(raw_id).decode('utf-8') if not raw_id.isdigit() else raw_id

                if candidate_id in existing_ids: continue
                logger.info(f"Processing new candidate ID: {candidate_id}")
                
                self.driver.execute_script("window.open(arguments[0]);", url)
                self.driver.switch_to.window(self.driver.window_handles[-1])
                try:
                    original_windows = set(self.driver.window_handles)
                    self.wait.until(EC.element_to_be_clickable((By.XPATH, DOWNLOAD_PDF_BUTTON_XPATH))).click()
                    try: WebDriverWait(self.driver, 3).until(EC.alert_is_present()).accept()
                    except TimeoutException: pass
                    new_window = WebDriverWait(self.driver, 15).until(
                        lambda d: next((w for w in d.window_handles if w not in original_windows), False)
                    )
                    self.driver.switch_to.window(new_window)
                    self.wait.until(EC.visibility_of_element_located((By.XPATH, DOWNLOAD_PAGE_CONTENT_VERIFICATION_XPATH)))
                    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
                    pdf_filename = f"{rank.replace(' ', '-')}_{ship_type.replace(' ', '-')}_{candidate_id}_{timestamp}.pdf"
                    if self._save_page_as_pdf(target_folder, pdf_filename):
                        logger.info(f"  -> Saved: {pdf_filename}")
                        existing_ids.add(candidate_id)
                except Exception as e:
                    logger.error(f"  -> Error on candidate {candidate_id}: {str(e)}")
                finally:
                    windows_to_close = [h for h in self.driver.window_handles if h != main_window]
                    for handle in windows_to_close:
                        try:
                            self.driver.switch_to.window(handle)
                            self.driver.close()
                        except NoSuchWindowException:
                            logger.warning(f"Window {handle} was already closed.")
                    self.driver.switch_to.window(main_window)
            
            try:
                next_button = self.driver.find_element(By.XPATH, NEXT_PAGE_BUTTON_XPATH)
                self.driver.execute_script("arguments[0].click();", next_button)
                self.wait.until(EC.staleness_of(current_table))
                page_number += 1
            except (NoSuchElementException, TimeoutException):
                logger.info("No 'Next' button found. Finished with this list.")
                break

    def download_resumes(self, rank, ship_type, force_redownload, logger):
        success = True
        message = ""
        try:
            # THE FIX: Always start from the dashboard to ensure lists are found
            logger.info("Navigating to dashboard to begin...")
            self.driver.get(DASHBOARD_URL)
            self.wait.until(EC.element_to_be_clickable((By.XPATH, CANDIDATE_COUNT_LINKS_XPATH)))

            logger.info("Finding all available candidate lists on dashboard...")
            links = self.driver.find_elements(By.XPATH, CANDIDATE_COUNT_LINKS_XPATH)
            lists_to_process = [{'name': link.find_element(By.XPATH, "./ancestor::h2/strong").text, 'url': link.get_attribute('href')} for link in links if "Today Downloads" not in link.find_element(By.XPATH, "./ancestor::h2/strong").text]
            
            logger.info(f"Found {len(lists_to_process)} lists to process.")
            
            rank_folder_name = rank.replace(' ', '_').replace('/', '-')
            target_folder = os.path.join(self.base_download_folder, rank_folder_name)
            existing_ids = set()
            if not force_redownload and os.path.exists(target_folder):
                 for filename in os.listdir(target_folder):
                    match = re.search(r'_(\d+)_', filename)
                    if match: existing_ids.add(match.group(1))

            for candidate_list in lists_to_process:
                logger.info(f"\n--- Processing List: {candidate_list['name']} ---")
                self.driver.get(candidate_list['url'])
                
                old_table = self.wait.until(EC.presence_of_element_located((By.ID, RESULTS_TABLE_ID)))
                
                Select(self.wait.until(EC.presence_of_element_located((By.ID, RANK_FILTER_ID)))).select_by_visible_text(rank)
                Select(self.wait.until(EC.presence_of_element_located((By.ID, SHIP_TYPE_FILTER_ID)))).select_by_visible_text(ship_type)
                self.driver.find_element(By.ID, FILTER_SUBMIT_BUTTON_ID).click()
                
                self.wait.until(EC.staleness_of(old_table))
                
                self._process_single_list(logger, rank, ship_type, target_folder, existing_ids)
            
            message = "Download process completed for all lists."
            logger.info(message)

        except Exception as e:
            message = f"An error occurred during download: {str(e)}"
            logger.error(message, exc_info=True)
            success = False
        
        log_content_for_ui = []
        with open(logger.handlers[0].baseFilename, 'r') as f:
            log_content_for_ui = f.read().splitlines()

        return {"success": success, "log": log_content_for_ui, "message": message}

    def quit(self):
        if self.driver:
            try: self.driver.quit()
            except Exception as e: print(f"Warning: Minor error on browser shutdown: {e}")
