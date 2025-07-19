# agent_headless.py
# This is a "headless" version of the Twitter Expert Agent, designed to be run
# automatically on a server using services like GitHub Actions. It has no GUI.
# VERSION UPDATE: More robust and patient login sequence.

import time
import json
import urllib.request
import urllib.error
import urllib.parse
import random
import re
import os
import sys
import datetime

# Selenium Imports
try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.service import Service as EdgeService
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.common.keys import Keys
    from selenium.common.exceptions import TimeoutException, NoSuchElementException, StaleElementReferenceException, ElementClickInterceptedException
    webdriver_available = True
except ImportError:
    print("Error: Selenium library not found. Please install it with 'pip install selenium'")
    sys.exit(1)

# Other Library Imports
try:
    import docx
    docx_available = True
except ImportError:
    docx_available = False


class HeadlessTwitterAgent:
    TWITTER_CHAR_LIMIT = 280

    def __init__(self, config, secrets):
        self.config = config
        self.secrets = secrets
        self.driver = None
        self.tweet_history = []
        self._log_message("Headless agent initialized.")

    def _log_message(self, message, level="INFO"):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{level}] - {message}")

    def _setup_driver(self):
        self._log_message("Setting up headless browser...")
        options = webdriver.EdgeOptions()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("window-size=1920,1080")
        try:
            # On GitHub Actions, the driver is usually in the system PATH
            self.driver = webdriver.Edge(options=options)
            self._log_message("Headless browser started successfully.")
            return True
        except Exception as e:
            self._log_message(f"CRITICAL: Could not start headless browser: {e}", "ERROR")
            return False

    def _login_to_twitter(self):
        self._log_message("Attempting to log in to X.com...")
        try:
            self.driver.get("https://x.com/login")
            wait = WebDriverWait(self.driver, 20)
            
            # Step 1: Enter username
            self._log_message("Waiting for username/email input field...")
            user_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='text']")))
            self._log_message("Entering username...")
            user_input.send_keys(self.secrets['TWITTER_USERNAME'])
            time.sleep(0.5) # Human-like pause
            
            # Step 2: Click the "Next" button
            self._log_message("Finding and clicking 'Next' button...")
            next_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[.//span[text()='Next']]")))
            next_button.click()
            
            # Step 3: Enter password
            self._log_message("Waiting for password input field...")
            pass_input = wait.until(EC.presence_of_element_located((By.XPATH, "//input[@name='password']")))
            self._log_message("Entering password...")
            pass_input.send_keys(self.secrets['TWITTER_PASSWORD'])
            time.sleep(0.5) # Human-like pause
            
            # Step 4: Click the "Log in" button
            self._log_message("Finding and clicking 'Log in' button...")
            login_button = wait.until(EC.element_to_be_clickable((By.XPATH, "//div[@data-testid='LoginForm_Login_Button']")))
            login_button.click()

            # Step 5: Verify login by waiting for the home timeline
            self._log_message("Waiting for home feed to load...")
            wait.until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='primaryColumn']")))
            self._log_message("Login successful.", "SUCCESS")
            return True
        except Exception as e:
            self._log_message(f"Login failed: {e}", "ERROR")
            self.driver.save_screenshot("login_error.png")
            return False

    def run_action_cycle(self):
        """Performs a single action cycle: post or reply."""
        if not self._setup_driver(): return
        
        if self._login_to_twitter():
            self._log_message("--- New Action Cycle ---", "HEAD")
            mode = self.config.get("action_mode", "strategic_mix")
            
            action_type = 'post'
            if mode == 'strategic_mix': action_type = 'reply' if random.random() < 0.40 else 'post'
            elif mode == 'reply_only': action_type = 'reply'

            if action_type == 'reply':
                self.perform_reply_action()
            else:
                self.perform_post_action(mode)
        
        self._shutdown_browser()

    def perform_post_action(self, mode):
        generation_function = self._get_generation_function(mode)
        if not generation_function: return
        content_package = generation_function()
        if not content_package: self._log_message("Content engine failed to return content.", "WARN"); return
        
        text = content_package.get("text")
        if text:
            text = self._truncate_or_summarize(text)
            image_path = None
            if self.config.get("attach_image", False):
                image_query = content_package.get("query_for_image", text)
                image_path = self._get_image_from_newsapi(image_query)
            
            if self.config.get("required_text"):
                text = f"{text}\n\n{self.config['required_text']}"

            self._post_tweet_in_browser(text, image_path=image_path)
            self._cleanup_temp_image(image_path)

    def perform_reply_action(self):
        target = self._find_tweet_to_engage_with()
        if not target: self._log_message("Could not find content to engage with.", "WARN"); return
        
        prompt = self._create_engagement_prompt(target)
        response_json = self.call_ai_model(prompt)
        if response_json:
            try:
                text = json.loads(response_json).get('tweet_text')
                if text:
                    text = self._truncate_or_summarize(text)
                    if self.config.get("required_text"):
                        text = f"{text}\n\n{self.config['required_text']}"
                    self._reply_on_twitter(target['url'], text)
            except (json.JSONDecodeError, AttributeError):
                self._log_message("Failed to parse AI JSON for engagement.", "ERROR")
    
    def call_ai_model(self, prompt, skip_json_parse=False):
        try:
            payload = {"contents": [{"role": "user", "parts": [{"text": prompt}]}]}
            api_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={self.secrets['GEMINI_API_KEY']}"
            req = urllib.request.Request(api_url, headers={'Content-Type': 'application/json'}, data=json.dumps(payload).encode('utf-8'))
            with urllib.request.urlopen(req, timeout=60) as response: result = json.loads(response.read().decode('utf-8'))
            if result.get('candidates') and result['candidates'][0].get('finishReason') != 'SAFETY':
                text_response = result['candidates'][0]['content']['parts'][0]['text'].strip()
                return text_response if skip_json_parse else text_response.replace("```json", "").replace("```", "")
        except Exception as e: self._log_message(f"Error calling AI model: {e}", "ERROR")
        return None

    def _truncate_or_summarize(self, text):
        if len(text) <= self.TWITTER_CHAR_LIMIT: return text
        prompt = f"Summarize the following text to be well under {self.TWITTER_CHAR_LIMIT} characters for a tweet. Keep the original tone and key message.\n\nTEXT:\n---\n{text}"
        summarized_text = self.call_ai_model(prompt, skip_json_parse=True)
        if not summarized_text or len(summarized_text) > self.TWITTER_CHAR_LIMIT: return text[:self.TWITTER_CHAR_LIMIT - 3] + "..."
        return summarized_text

    def _get_generation_function(self, mode):
        if self.config.get("auto_niche", False): return self._analyze_and_generate_from_global_trends
        source_map = {'strategic_mix': self._analyze_and_generate_from_global_trends, 'post_only_controversy': self._analyze_and_generate_from_global_trends, 'post_only_news': self._generate_from_news_article, 'post_only_word': self._generate_from_word_file}
        return source_map.get(mode)

    def _get_image_from_newsapi(self, query):
        api_key = self.secrets.get("NEWSAPI_KEY")
        if not api_key: return None
        try:
            params = {'q': query, 'apiKey': api_key, 'pageSize': 20, 'language': 'en', 'sortBy': 'relevancy'}
            req = urllib.request.Request(f"https://newsapi.org/v2/everything?{urllib.parse.urlencode(params)}", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=20) as response: api_data = json.loads(response.read())
            if api_data.get("status") != "ok" or not api_data.get("articles"): return None
            articles_with_images = [article for article in api_data["articles"] if article.get("urlToImage")]
            if not articles_with_images: return None
            return self._download_image(random.choice(articles_with_images)['urlToImage'])
        except Exception as e: self._log_message(f"Error fetching image from NewsAPI: {e}", "ERROR")
        return None

    def _download_image(self, image_url):
        try:
            image_path = os.path.join(os.getcwd(), "temp_post_image.jpg")
            opener = urllib.request.build_opener(); opener.addheaders = [('User-Agent', 'Mozilla/5.0')]; urllib.request.install_opener(opener)
            urllib.request.urlretrieve(image_url, image_path)
            if os.path.exists(image_path): return image_path
        except Exception as e: self._log_message(f"Failed to download image: {e}", "ERROR")
        return None

    def _cleanup_temp_image(self, image_path):
        if image_path and os.path.exists(image_path):
            try: os.remove(image_path)
            except Exception as e: self._log_message(f"Could not delete temp image: {e}", "WARN")

    def _generate_from_news_article(self):
        api_key = self.secrets.get("NEWSAPI_KEY"); niche = self.config.get("niche")
        if not api_key: return None
        try:
            params = {'q': niche, 'apiKey': api_key, 'pageSize': 50, 'language': 'en', 'sortBy': 'publishedAt'}
            api_url = f"https://newsapi.org/v2/everything?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response: api_data = json.loads(response.read())
            if api_data.get("status") != "ok" or not api_data.get("articles"): return None
            headline = random.choice(api_data["articles"]).get('title')
            if not headline: return None
            task = f"Analyze this news headline: '{headline}'. Formulate an insightful tweet about it."
            prompt = self._create_master_prompt(task, self.config.get("tone"), niche)
            response_json = self.call_ai_model(prompt)
            if not response_json: return None
            text = json.loads(response_json).get('tweet_text')
            return {"text": text, "query_for_image": headline}
        except Exception as e: self._log_message(f"Error in News engine: {e}", "ERROR"); return None
        
    def _analyze_and_generate_from_global_trends(self):
        try:
            self.driver.get("https://x.com/explore/tabs/trending")
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.XPATH, "//div[@data-testid='trend']"))); time.sleep(3)
            trends_text_list = [elem.text for elem in self.driver.find_elements(By.XPATH, "//div[@data-testid='trend']") if elem.text]
            if not trends_text_list: return None
            trends_blob = "\n".join(trends_text_list)
            task = f"From these trends, find the most interesting one and write an engaging tweet:\n{trends_blob}"
            prompt = self._create_master_prompt(task, self.config.get("tone"), "Current Events")
            response_json = self.call_ai_model(prompt)
            if not response_json: return None
            text = json.loads(response_json).get('tweet_text')
            analysis = json.loads(response_json).get('analysis')
            return {"text": text, "query_for_image": analysis or text}
        except Exception as e: self._log_message(f"Error in Trend Analysis engine: {e}", "ERROR"); return None
        
    def _generate_from_word_file(self):
        filepath = self.config.get("word_file_path")
        if not filepath or not os.path.exists(filepath): return None
        try:
            if not docx_available: return None
            doc = docx.Document(filepath); content = '\n'.join([para.text for para in doc.paragraphs])
            task = f"Create a compelling tweet that captures the main idea of this text:\n---\n{content[:4000]}"
            prompt = self._create_master_prompt(task, self.config.get("tone"), "document analysis")
            response_json = self.call_ai_model(prompt)
            if not response_json: return None
            text = json.loads(response_json).get('tweet_text')
            return {"text": text, "query_for_image": text[:100]}
        except Exception as e: self._log_message(f"Error reading Word file: {e}", "ERROR"); return None

    def _post_tweet_in_browser(self, text, image_path=None):
        try:
            self.driver.get("https://x.com/compose/post")
            text_area = WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.XPATH, '//div[@data-testid="tweetTextarea_0"]')))
            if image_path:
                self.driver.find_element(By.XPATH, "//input[@data-testid='fileInput']").send_keys(image_path)
                time.sleep(10)
            text_area.send_keys(text)
            time.sleep(1)
            post_button = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='tweetButton']")))
            post_button.click()
            self._log_message("Tweet sent.", "SUCCESS"); time.sleep(5)
        except Exception as e: self._log_message(f"Error posting on Twitter: {e}", "ERROR")

    def _reply_on_twitter(self, url, text):
        try:
            self.driver.get(url)
            reply_button = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, "(//article[@data-testid='tweet']//button[@data-testid='reply'])[1]")))
            reply_button.click()
            reply_area = WebDriverWait(self.driver, 10).until(EC.element_to_be_clickable((By.XPATH, '//div[@data-testid="tweetTextarea_0"]')))
            reply_area.send_keys(text); time.sleep(1)
            reply_button_final = WebDriverWait(self.driver, 20).until(EC.element_to_be_clickable((By.XPATH, "//button[@data-testid='tweetButton']")))
            reply_button_final.click()
            self._log_message("Reply sent.", "SUCCESS"); time.sleep(5)
        except Exception as e: self._log_message(f"Error replying on Twitter: {e}", "ERROR")
        
    def _find_tweet_to_engage_with(self):
        try:
            niche = self.config.get("niche", "")
            search_url = f"https://x.com/search?q={urllib.parse.quote(niche)}&src=typed_query&f=live" if niche else "https://x.com/home"
            self.driver.get(search_url)
            WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.XPATH, "//article[@data-testid='tweet']"))); time.sleep(2)
            articles = self.driver.find_elements(By.XPATH, "//article[@data-testid='tweet']")
            for article in random.sample(articles, min(len(articles), 10)):
                try:
                    if "promoted" not in article.text.lower():
                        return {'author': article.find_element(By.XPATH, ".//div[@data-testid='User-Name']//span[contains(text(), '@')]").text.strip('@'), 'text': article.find_element(By.XPATH, ".//div[@data-testid='tweetText']").text, 'url': article.find_element(By.XPATH, ".//a[contains(@href, '/status/')]").get_attribute('href')}
                except NoSuchElementException: continue
        except Exception as e: self._log_message(f"Error finding tweet: {e}", "ERROR")
        return None
        
    def _create_engagement_prompt(self, target):
        niche = self.config.get("niche", ""); personality = self.config.get("tone", "Thought Leader")
        task = f"You've found a tweet from @{target['author']} that says: \"{target['text']}\"\nYour task is to write a valuable, insightful reply."
        return self._create_master_prompt(task, personality, niche, is_reply=True)
        
    def _create_master_prompt(self, task, personality, niche, is_reply=False):
        history_context = "\n".join(self.tweet_history) if self.tweet_history else "No recent activity."
        output_json = '"analysis": "A brief summary.", "tweet_text": "The final tweet text."'
        if is_reply: output_json = '"analysis": "Justification for your reply.", "tweet_text": "The reply text. DO NOT use @ mentions."'
        main_task = f"**TASK:** {task}"
        return f"""**Persona:** You are a '{personality}' expert in '{niche}'.\n**Your Recent Activity:**\n{history_context}\n{main_task}\n**Output Format (Strictly JSON):**\n```json\n{{\n  {output_json}\n}}\n```"""

    def _shutdown_browser(self):
        if self.driver: self.driver.quit()

if __name__ == "__main__":
    try:
        with open('config.json', 'r') as f:
            config = json.load(f)
            
        secrets = {
            "GEMINI_API_KEY": os.environ.get('GEMINI_API_KEY'),
            "NEWSAPI_KEY": os.environ.get('NEWSAPI_KEY'),
            "TWITTER_USERNAME": os.environ.get('TWITTER_USERNAME'),
            "TWITTER_PASSWORD": os.environ.get('TWITTER_PASSWORD')
        }

        if not all(secrets.values()):
            print("ERROR: One or more required secrets are not set in the environment.")
            sys.exit(1)

        agent = HeadlessTwitterAgent(config, secrets)
        agent.run_action_cycle()

    except FileNotFoundError:
        print("ERROR: config.json not found. Please create it.")
    except Exception as e:
        print(f"A fatal error occurred: {e}")
