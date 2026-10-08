import requests
import bs4
import re
import sqlite3
import pandas as pd
from pathlib import Path
import shutil
import os
import time
from urllib.parse import urlparse
from playwright.sync_api import Playwright, sync_playwright, expect
import pytesseract as tess
from PIL import Image
import sys
from roman_numerals import RomanNumeral, InvalidRomanNumeralError
from tenacity import retry, wait_exponential, retry_if_exception_type, stop_after_attempt
import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--databasename", type=str, default='gallica_database', help = "Name of database file.")
#parser.add_argument("--sheetname", type=str, help = "Name of Excel sheet.")
parser.add_argument("--ext", type=str, default='db', help = "Name of extension (no dot).")
args = parser.parse_args()

class PlaywrightHTTPlikeError(Exception):
    pass

class RetronewsError(Exception):
    pass

user_agent =  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"

databasename = f'{args.databasename.strip()}.{args.ext.strip()}'
filtered_gallica_electricity = 'filtered_gallica_electricity'
custom_headers = {"User-Agent": user_agent}  #insert your user_agent
response_Path = os.path.join(Path.home()/'Desktop', f"{os.path.basename(databasename.split(".")[0])}_response")
response_Path_images = os.path.join(Path.home()/'Desktop', f"{os.path.basename(databasename.split(".")[0])}_response_images")
os.makedirs(response_Path, exist_ok=True)
os.makedirs(response_Path_images, exist_ok=True)

def create_filtered_database_table(databasename, filtered_database_name):
    with sqlite3.connect(databasename, isolation_level = None) as conn:
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS {filtered_database_name}")
        x = cursor.execute('SELECT name FROM sqlite_schema WHERE type="table"').fetchall() #[('name1'), ('name2'), ...] But here it needs to have only the first table, so I will take the first; otherwise, the program shall exit this function and assumes you already made the filtered database
        tables = [row[0] for row in x]
        for table in tables:
            if table != filtered_database_name and table.startswith("sqlite_") == False:
                y = cursor.execute(f'PRAGMA table_info({table})').fetchall()
                columns = [col[1] for col in y]   #What if the code doesn't have rowid? Then go cry to your mama
                pattern = r'\s*(?:N|n)(?:Q|q)(?:A|a)_*(?:M|m)[a-zA-Z0-9]_*\s*'
                to_be_removed = next((k for k in columns if re.match(pattern, k)), None)
                try:
                    columns.remove(to_be_removed)
                    sentence = ", ".join(columns)
                    cursor.execute(f'CREATE TABLE IF NOT EXISTS {filtered_database_name} AS SELECT {sentence}, MAX(NQA_Moyen_) AS max_NQA_Moyen FROM {table} GROUP BY AUTEURS, CONTRIBUTEURS, EDITEURS, DATES, ARK_CATALOGUE')
#                   cursor.commit()
#                    test = cursor.execute(f"SELECT * FROM {filtered_database_name} ORDER BY DATES ASC LIMIT 100")
#                    for i in test:
#                        print(i)
                except Exception as e:
                    print(e)
                    sys.exit(1)
            else:
                continue

def unified_date(date_str):
    pattern_list = [r'(\d{4})', r'(\d{4})-+\d{1,2}-+\d{1,2}', r'(\d{4})-+(\d{4})']
    range_bool = False
    for i, pattern in enumerate(pattern_list):
        search_obj = re.search(pattern, date_str, re.IGNORECASE)
        if search_obj is None:
            continue
        elif search_obj is not None and i == 2:
            range_bool = True
            return str(f"{search_obj.group(1)}_{search_obj.group(2)}"), range_bool
        elif search_obj is not None and i != 2:
            return str(search_obj.group(1)), range_bool

def find_ark_num_gallica(url):
    parse = urlparse(url)
    ark_num_str = parse.path.split("/")[-1]
    return ark_num_str

@retry(
    retry=retry_if_exception_type((requests.exceptions.HTTPError, requests.exceptions.ConnectionError)),
    wait=wait_exponential(multiplier=1, min=2, max=1000),
    stop=stop_after_attempt(10),
    reraise = True
)
def get_last_page_function_gallica(session: requests.Session, url):
    parse= urlparse(url)
    ark_part = find_ark_num_gallica(url=url)
    url_pagenum = parse.scheme + '://' + parse.netloc + f'/services/Pagination?ark={ark_part}'
    print(url_pagenum)
    req_pagenum = session.get(url_pagenum)
    req_pagenum.raise_for_status()
    soup_pagenum = bs4.BeautifulSoup(req_pagenum.content, features='xml')
    last_page = int(soup_pagenum.select('livre page ordre')[-1].getText())  #the last numbered page one can access in a Gallica document. It always starts at 1.
    print(last_page)
    return last_page

@retry(
    retry=retry_if_exception_type((PlaywrightHTTPlikeError, requests.exceptions.ConnectionError)), 
    wait=wait_exponential(multiplier=1, min=2, max=20),
    stop=stop_after_attempt(7),
    reraise = True
)
def get_textebrut_gallica(page, url, file):
    retronews_bool = False
    url_textebrut = url + ".texteBrut"
    print(url_textebrut)
    resp = page.goto(url_textebrut) #change of url, capturing response
    page.wait_for_load_state("networkidle", timeout=60000)
    if "altcha" in page.url:
        page.locator("svg").first.click()
    while True:
        if "altcha" in page.url:
            page.wait_for_timeout(8000)
        else:
            break
    page.wait_for_load_state("networkidle", timeout=60000)
    current_url = page.url
    print(current_url)
    if resp is None or not resp.ok:
        status = resp.status if resp else "No Response"
        status_text = resp.status_text if resp else "Timeout/Blocked"
        raise PlaywrightHTTPlikeError(f"HTTP {status}: {status_text} to {current_url}")
    page.wait_for_timeout(1000)
    if "altcha" in page.url:
        raise PlaywrightHTTPlikeError("Stuck on Altcha challenge.")
    if "retronews" in page.url:
        retronews_bool = True
        return retronews_bool
    list_of_text = page.locator("p").all_inner_texts()
    ocr_text_plus_head = "\n".join(list_of_text)
    target_text = ocr_text_plus_head.split("Date de mise en ligne")[-1]
    file.write(target_text)

@retry(
    retry=retry_if_exception_type((requests.exceptions.HTTPError, RetronewsError, requests.exceptions.ConnectionError)), 
    wait=wait_exponential(multiplier=1, min=2, max=160),
    stop=stop_after_attempt(7),
    reraise = True
)
def gallica_ocr(session: requests.Session, url, file, ark_num_image, path_for_images, last_page, i):
    print("Inside OCR")
    time.sleep(5)
    url_image = url + f"/f{i}.highres"
    print(url_image)
    res = session.get(url_image)
    time.sleep(5)
    res.raise_for_status()
    if "retronews" in res.url:
        raise RetronewsError("Got another Retronews website, ignoring for now")
    with open(Path(path_for_images)/f'{ark_num_image}', 'wb') as image_file:
        for chunk in res.iter_content(100000):
            image_file.write(chunk)
        time.sleep(1)
    img = Image.open(Path(path_for_images)/f'{ark_num_image}')
    custom_config = r"--psm 3 --dpi 500"
    text = tess.image_to_string(img, config=custom_config, lang='fra')
    print(f"I'm in page {i}")
    file.write(text)

def fixing_year(path_with_year, ark_num, response_Path, date):
    try:
        with open(Path(path_with_year)/f'{ark_num}.txt', 'r', encoding = "UTF-8") as read_file:
            text = read_file.read()
            obj_pattern = r'(?:\b|\s)*(M)\.\s*(DCC)\.\s*(\w{1,4})(?:\.|\,|\s|\b)*' #it's always 17... in my case. A more general one would be r'(?:\b|\s)(\w{1})\.\s**\w{1,4})\.\s*(\w{1,4})'. However, you should be careful. You're messing with regex!
            obj_search = re.search(obj_pattern, text, re.IGNORECASE)
            if obj_search is None:
                print("No Match object found")
                return None
            anno = str(obj_search.group(1)).strip("\n").strip().upper() + str(obj_search.group(2)).strip("\n").strip().upper() + str(obj_search.group(3)).strip("\n").strip().upper()
            corrected_date = int(RomanNumeral.from_string(anno))  #an integer
        if int(date.split("_")[0]) == corrected_date:
            print(date, corrected_date, "Same years.")
        source = Path(path_with_year)/f'{ark_num}.txt'
        destination = response_Path / f"{corrected_date}"
        os.makedirs(destination, exist_ok=True)
        shutil.move(source, destination/f'{ark_num}.txt')
        return corrected_date
    except InvalidRomanNumeralError as e:
        return f"{e}, {anno}, {ark_num}"

def check_if_file_empty(path_with_year, ark_num):
    marker = False
    with open(Path(path_with_year)/f'{ark_num}.txt', 'r', encoding = "UTF-8") as file_target:
        text = file_target.read()
        pattern_1 = r'(?:\s|\b)*(?:L|l)a\s*ressource\s*\w{6,9}\s*n(?:\'\')*est\s*pas(?:\s*)disponible(?:\.|\s|\b)*'
        pattern_2 = r':\s*429\s*:*\s*trop\s*de(?:s*)\s*re'
        pattern_3 = r'^\s*$'
        match_obj = re.search(pattern_1, text, re.IGNORECASE)
        match_obj_2 = re.search(pattern_2, text, re.IGNORECASE)
        match_obj_3 = re.search(pattern_3, text, re.IGNORECASE)
        if match_obj is not None or match_obj_2 is not None or match_obj_3 is not None or text == '':
            marker = True
            return marker
        else:
            return marker

def main_function(custom_headers, databasename, response_Path, response_Path_images, filtered_databasename_table):
    os.makedirs(response_Path, exist_ok=True)
    os.makedirs(response_Path_images, exist_ok=True)
    num_of_links = 0
    num_weird = 0
    retronews_num = 0
    missing_pages = 0
    num_of_pages = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        with sqlite3.connect(databasename, isolation_level = None) as conn:
            cursor = conn.cursor()
            list_urls_nqa_date = cursor.execute(f'SELECT URL, max_NQA_Moyen, DATES, TITRE, SOURCE FROM {filtered_databasename_table} LIMIT 10').fetchall() #[(url, nqa, date), (url, nqa, date), ...]
            with requests.Session() as my_session:
                my_session.headers.update(custom_headers)
#                try:
                for url, nqa, date_raw, title, source in list_urls_nqa_date:
                    num_of_links += 1
                    date, range_bool = unified_date(date_raw)
                    path_with_year = os.path.join(response_Path, f"{date}")
                    path_for_images = os.path.join(response_Path_images, f"{date}")
                    os.makedirs(path_with_year, exist_ok=True)
                    os.makedirs(path_for_images, exist_ok=True)
                    if 'gallica' in url:
                        time.sleep(1)
                        ark_num = find_ark_num_gallica(url=url)
                        try:
                            last_page = get_last_page_function_gallica(my_session, url=url)
                        except (IndexError, requests.exceptions.HTTPError, requests.exceptions.ConnectionError) as e:
                            num_weird += 1
                            print(f"No last page found, broken link: {e}")
                            continue
                        if int(nqa) > 70:
                            time.sleep(1)
                            try:
                                with open(Path(path_with_year)/f'{ark_num}.txt', 'w', encoding = "UTF-8") as my_file:
                                    retronews = get_textebrut_gallica(page=page, url=url, file=my_file)
                                if bool(retronews) == True:
                                    retronews_num += 1
                                    print(f"Retronews website. Ignoring (for now)... So far: {retronews_num}.")
                            except (requests.exceptions.HTTPError, PlaywrightHTTPlikeError, requests.exceptions.ConnectionError) as e:
                                print(f"Definitely didn't work: {e}")
                            #function for text: check_if_file_empty
                            marker_bool = check_if_file_empty(path_with_year=path_with_year, ark_num=ark_num)
                            #If no real text got taken, then we do our own OCR
                            print(marker_bool)
                            if bool(marker_bool) == True:
                                RetronewsError
                                try:
                                    with open(Path(path_with_year)/f'{ark_num}.txt', 'w', encoding = "UTF-8") as file:
                                        for j in range(1, last_page + 1):
                                            num_of_pages += 1
                                            print(num_of_pages) #total number of pages, missing or otherwise
                                            try:
                                                gallica_ocr(session=my_session, url=url, file=file, ark_num_image=ark_num, path_for_images=path_for_images, last_page=last_page, i=j)
                                            except requests.exceptions.HTTPError as e:
                                                missing_pages += 1
                                                print(title)
                                                print(f"HTTPError: {e}")
                                                print(f"Number of pages missing: {missing_pages}")
                                                continue
                                except (RetronewsError, requests.exceptions.ConnectionError) as e:
                                    retronews_num += 1
                                    print(f"Custom error: {e}. So far: {retronews_num} retronews websites")
                            #now we change the folder for the correct year, in case of a range of years. Don't change this order, or the functions above won't be able to find the .txt
                            if range_bool == True:
                                correct_year = fixing_year(path_with_year=path_with_year, ark_num=ark_num, response_Path=response_Path, date=date)
                                print(correct_year)
                        elif int(nqa) <= 70:
                            time.sleep(3)
                            with open(Path(path_with_year)/f'{ark_num}.txt', 'w', encoding = "UTF-8") as file:
                                gallica_ocr(session=my_session, url=url, file=file, ark_num_image=ark_num, path_for_images=path_for_images, last_page=last_page)
                            if range_bool == True:
                                correct_year = fixing_year(path_with_year=path_with_year, ark_num=ark_num, response_Path=response_Path, date=date)
                                print(correct_year)
                    else:
                        print(f"URL from website different from Gallica: {url}. Title is: {title}. Source is: {source}")
                        num_weird += 1
                        label = lambda n: "link" if n == 1 else "links"
                        print(f"Number of weird, non-Gallica {label(num_weird)}: {num_weird}")
                        continue
                    
    return num_of_links, num_weird, retronews_num, missing_pages, num_of_pages
#                except (requests.exceptions.HTTPError, PlaywrightHTTPlikeError) as e:
#                    print(f"Definetily didn't work: {e}")
#                except Exception as e:
#                    print(f"Error: {e}")

if __name__ == "__main__":
    create_filtered_database_table(databasename=databasename, filtered_database_name=filtered_gallica_electricity)
    num_of_links, num_weird, retronews_num, missing_pages, num_of_pages = main_function(custom_headers=custom_headers, databasename=databasename, response_Path=response_Path, response_Path_images=response_Path_images, filtered_databasename_table=filtered_gallica_electricity)
    final_sentence = f"Number of links computed (processed or otherwise): {num_of_links}. Number of weird, non-processed links: {num_weird+retronews_num}. Percentage (links): {(num_weird + retronews_num) / num_of_links:.2%}. Total number of pages tried: {num_of_pages}. Number of missing pages: {missing_pages}. Percentage (pages): {num_of_pages/missing_pages:.2%}"
    print(final_sentence)