import os
import json
import re
import pandas as pd
import anthropic


def _extract_json_array(text: str):
    text = text.strip()

    if "```" in text:
        parts = text.split("```")
        if len(parts) > 1:
            text = parts[1]

    text = text.strip()

    if text.lower().startswith("json"):
        text = text[4:].strip()

    start = text.find("[")
    end = text.rfind("]") + 1

    if start == -1 or end == 0:
        raise ValueError("לא נמצא מערך JSON תקין")

    text = text[start:end].strip()
    text = re.sub(r",\s*]", "]", text)
    text = re.sub(r",\s*}", "}", text)

    return json.loads(text)


def analyze_contracts_file(file_path: str) -> pd.DataFrame:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise Exception("אין API KEY")

    client = anthropic.Anthropic(api_key=api_key)

    raw_df = pd.read_excel(file_path)
    text = raw_df.to_string(index=False)

    prompt = f"""
יש לי טבלת הסכמים.
הטבלה יכולה לכלול לקוחות רגילים, יומיים, שעות נוספות, תקנים שונים, ואתרים.

הטבלה:
{text}

תחזיר JSON בלבד, בלי שום טקסט נוסף, בפורמט:
[
  {{
    "לקוח": "",
    "אתר": "",
    "סוג": "שעות/ימים/נוספות",
    "מחיר": 0,
    "תקן": 0
  }}
]

חוקים:
1. אם יש "יומי" או "יומיים" → סוג = "ימים"
2. אם יש "שעות נוספות" או "נוספות" → סוג = "נוספות"
3. אחרת → סוג = "שעות"
4. אם יש תקן כמו 236 או 220 → תקן = המספר
5. אם אין תקן → 0
6. אם אין אתר → אתר = ""
7. מחיר חייב להיות מספר
8. תחזיר JSON תקין בלבד
"""

    res = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=4000,
        system="Return only valid JSON array. No markdown. No explanation.",
        messages=[{"role": "user", "content": prompt}]
    )

    content = res.content[0].text.strip()

    try:
        data = _extract_json_array(content)
    except Exception:
        df = raw_df.copy()

        client_col = None
        site_col = None
        price_col = None
        standard_col = None

        for col in df.columns:
            c = str(col)
            if client_col is None and "לקוח" in c:
                client_col = col
            if site_col is None and "אתר" in c:
                site_col = col
            if price_col is None and "מחיר" in c:
                price_col = col
            if standard_col is None and "תקן" in c:
                standard_col = col

        if client_col is None or price_col is None:
            raise Exception("AI נכשל וגם לא ניתן היה לזהות עמודות בסיסיות בקובץ ההסכמים")

        fallback_df = pd.DataFrame()
        fallback_df["לקוח"] = df[client_col].fillna("").astype(str)
        fallback_df["אתר"] = df[site_col].fillna("").astype(str) if site_col else ""
        fallback_df["מחיר"] = pd.to_numeric(df[price_col], errors="coerce").fillna(0)
        fallback_df["תקן"] = pd.to_numeric(df[standard_col], errors="coerce").fillna(0) if standard_col else 0

        def detect_type(name):
            name = str(name)
            if "יומ" in name:
                return "ימים"
            if "נוספ" in name:
                return "נוספות"
            return "שעות"

        fallback_df["סוג"] = fallback_df["לקוח"].apply(detect_type)
        data = fallback_df[["לקוח", "אתר", "סוג", "מחיר", "תקן"]].to_dict(orient="records")

    df = pd.DataFrame(data)

    required_cols = ["לקוח", "אתר", "סוג", "מחיר", "תקן"]
    for col in required_cols:
        if col not in df.columns:
            df[col] = "" if col in ["לקוח", "אתר", "סוג"] else 0

    df["לקוח"] = df["לקוח"].fillna("").astype(str)
    df["אתר"] = df["אתר"].fillna("").astype(str)
    df["סוג"] = df["סוג"].fillna("שעות").astype(str)
    df["מחיר"] = pd.to_numeric(df["מחיר"], errors="coerce").fillna(0)
    df["תקן"] = pd.to_numeric(df["תקן"], errors="coerce").fillna(0)

    return df[required_cols]
