import pandas as pd
import numpy as np
import requests
import re
import os
import json
import time
from tqdm import tqdm
from datetime import datetime, timedelta
from convertdate import hebrew
import logging

# ----------------------------
# Setup Logging
# ----------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ----------------------------
# Utility Functions
# ----------------------------
def remove_html_tags_and_entities(text):
    text = re.sub(r'<.*?>', '', text)
    text = re.sub(r'&[^;\s]+;', '', text)
    return text.strip()

def escape_latex_special_chars(text):
    return text.replace('\\', '\\textbackslash{}').replace('_', '\\_')

def hebrew_number(num):
    hebrew_letters = {
        1: 'א', 2: 'ב', 3: 'ג', 4: 'ד', 5: 'ה', 6: 'ו', 7: 'ז', 8: 'ח', 9: 'ט',
        10: 'י', 20: 'כ', 30: 'ל', 40: 'מ', 50: 'נ', 60: 'ס', 70: 'ע', 80: 'פ', 90: 'צ',
        100: 'ק', 200: 'ר', 300: 'ש', 400: 'ת'
    }
    if num == 15:
        return 'טו'
    if num == 16:
        return 'טז'
    result = ''
    if num >= 100:
        hundreds = (num // 100) * 100
        result += hebrew_letters.get(hundreds, '')
        num %= 100
    if num >= 10:
        tens = (num // 10) * 10
        result += hebrew_letters.get(tens, '')
        num %= 10
    if num:
        result += hebrew_letters.get(num, '')
    return result

# ----------------------------
# Prompt & Generate CSV + ICS
# ----------------------------
def generate_schedule_csv():
    name = input("Enter child's name: ").strip()
    dob = input("Enter Gregorian birthdate (YYYY-MM-DD): ").strip()
    try:
        y, m, d = map(int, dob.split("-"))
        birth = datetime(y, m, d)
    except:
        logger.error("Invalid date format.")
        return None

    h_y, h_m, h_d = hebrew.from_gregorian(y, m, d)
    print(f"Hebrew birthday: {h_d} {h_m} {h_y}")
    age = (datetime.now() - birth).days // 365
    print(f"Child is approx {age} years old")

    start = datetime(*hebrew.to_gregorian(h_y + 5, h_m, h_d))
    end = datetime(*hebrew.to_gregorian(h_y + 10, h_m, h_d)) - timedelta(days=1)  # inclusive
    print(f"Schedule will run from {start.date()} to {end.date()}")

    input_csv = "/Users/matthewmiller/Desktop/Desktop/Parsha Tracking Sheet - Chapters of Tanach (1).csv"
    if not os.path.exists(input_csv):
        logger.error(f"Missing: {input_csv}")
        return None
    df = pd.read_csv(input_csv)
    bible_df = df[df["Data Type"] == "Bible"]

    all_verses = []
    for _, row in bible_df.iterrows():
        verses = [f"{row['Book']} {row['Chapter']}:{v}" for v in range(1, row['Number of Verses or Mishnahs'] + 1)]
        all_verses.extend(verses)

    # Evenly split over total days
    total_days = (end - start).days + 1
    daily_counts = [len(all_verses) // total_days] * total_days
    for i in range(len(all_verses) % total_days):
        daily_counts[i] += 1  # distribute leftovers to the first few days

    portions = []
    idx = 0
    for i in range(total_days):
        day = start + timedelta(days=i)
        portion = all_verses[idx:idx + daily_counts[i]]
        portions.append((day, portion))
        idx += daily_counts[i]

    schedule = []
    for dt, verses in portions:
        schedule.append({
            "Date": dt.strftime("%Y-%m-%d"),
            "Day of Week": dt.strftime("%A"),
            "Bible": ", ".join(verses),
            "Bible Count": len(verses)
        })
    df_out = pd.DataFrame(schedule)

    csv_path = f"study_schedule_{name.replace(' ', '_')}_{dob}.csv"
    df_out.to_csv(csv_path, index=False)
    print(f"\n✅ CSV saved: {csv_path}")

    # ICS generation
    ics_path = csv_path.replace(".csv", ".ics")
    with open(ics_path, "w") as f:
        f.write("BEGIN:VCALENDAR\nVERSION:2.0\n")
        for i, row in df_out.iterrows():
            start_dt = datetime.strptime(row['Date'], "%Y-%m-%d")
            f.write("BEGIN:VEVENT\n")
            f.write(f"UID:{i}@study\n")
            f.write(f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}\n")
            f.write(f"DTSTART;VALUE=DATE:{start_dt.strftime('%Y%m%d')}\n")
            f.write(f"SUMMARY:Study {row['Bible Count']} verses\n")
            f.write(f"DESCRIPTION:{row['Bible']}\n")
            f.write("END:VEVENT\n")
        f.write("END:VCALENDAR")
    print(f"✅ ICS saved: {ics_path}")

    return csv_path

# ----------------------------
# Get verses from Sefaria
# ----------------------------
def get_sefaria_verse_entries(ref, retries=5, timeout=10):
    verse_entries = []
    for r in ref.split(','):
        r = r.strip()
        url = f"https://www.sefaria.org/api/texts/{r}?context=0"
        for attempt in range(retries):
            try:
                res = requests.get(url, timeout=timeout)
                if res.status_code == 200:
                    break
                time.sleep(2 ** attempt)
            except:
                time.sleep(2 ** attempt)
        else:
            logger.warning(f"Failed after {retries} tries: {r}")
            continue
        try:
            data = res.json()
            section = data.get("sections", [1, 1])
            he = data.get("he", [])
            if isinstance(he, list):
                for i, txt in enumerate(he, start=section[1]):
                    clean = remove_html_tags_and_entities(txt)
                    verse_entries.append((section[0], i, clean))
            else:
                clean = remove_html_tags_and_entities(he)
                verse_entries.append((section[0], section[1], clean))
        except Exception as e:
            logger.error(f"Error parsing {r}: {e}")
    return verse_entries

# ----------------------------
# Build .tex from schedule
# ----------------------------
def generate_latex_from_schedule(csv_path):
    tex_path = csv_path.replace(".csv", ".tex")
    checkpoint = tex_path + ".checkpoint"
    progress = tex_path + ".progress"

    if os.path.exists(progress):
        with open(progress) as f:
            last = int(f.read().strip())
            logger.info(f"Resuming from row {last + 1}")
    else:
        last = -1

    df = pd.read_csv(csv_path)
    if os.path.exists(checkpoint):
        with open(checkpoint, "r", encoding="utf-8") as f:
            tex = f.read()
    else:
        tex = r"""
\documentclass{article}
\usepackage[utf8]{inputenc}
\usepackage{geometry}
\usepackage{fontspec}
\geometry{margin=1in}
\usepackage{polyglossia}
\setmainlanguage{hebrew}
\newfontfamily\hebrewfont[Script=Hebrew]{Ezra SIL}
\begin{document}
\tableofcontents
\newpage
        """.strip() + "\n"

    for i, row in tqdm(df.iterrows(), total=len(df), initial=last + 1):
        if i <= last:
            continue

        date = row["Date"]
        ref = row["Bible"]
        heb_date = date
        tex += f"\n\\section*{{{date}}}\n\\subsection*{{תנ\"ך: {ref}}}\n"

        verses = get_sefaria_verse_entries(ref)
        for chap, num, text in verses:
            tex += f"\\noindent\\textbf{{\\textsuperscript{{{hebrew_number(num)}}}}} {escape_latex_special_chars(text)}\\\\\n"

        # Save progress every row
        with open(checkpoint, "w", encoding="utf-8") as f:
            f.write(tex)
        with open(progress, "w") as f:
            f.write(str(i))

    tex += "\n\\end{document}"
    with open(tex_path, "w", encoding="utf-8") as f:
        f.write(tex)

    # Cleanup
    if os.path.exists(checkpoint): os.remove(checkpoint)
    if os.path.exists(progress): os.remove(progress)

    print(f"\n✅ LaTeX file saved: {tex_path}")

# ----------------------------
# Main flow
# ----------------------------
if __name__ == "__main__":
    path = generate_schedule_csv()
    if path:
        generate_latex_from_schedule(path)