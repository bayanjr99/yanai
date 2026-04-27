import os
import json
import pandas as pd
import anthropic


def ask_ai_about_report(df: pd.DataFrame, question: str) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "אין API KEY"

    client = anthropic.Anthropic(api_key=api_key)

    try:
        prompt = f"""
יש לי דוח חיוב בטבלה הבאה:

{df.to_string(index=False)}

שאלת המשתמש:
{question}

תענה בעברית, ברור וקצר.
"""
        res = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system="אתה עוזר פיננסי שמנתח דוחות חיוב.",
            messages=[{"role": "user", "content": prompt}]
        )
        return res.content[0].text.strip()

    except Exception as e:
        return f"שגיאה ב-AI: {e}"


def detect_issues_with_ai(df: pd.DataFrame):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return []

    client = anthropic.Anthropic(api_key=api_key)

    prompt = f"""
יש לי דוח חיוב:

{df.to_string(index=False)}

תחזיר JSON בלבד, בלי שום טקסט נוסף, בפורמט:
[
  {{
    "שם לקוח": "",
    "אתר": "",
    "סוג בעיה": "",
    "פירוט": "",
    "המלצה": ""
  }}
]

אם אין בעיות תחזיר [] בלבד.

תזהה:
- מחיר 0
- חיוב 0 למרות שיש שעות
- השלמה חריגה
- ימים לא הגיוניים
- לקוח או אתר חסרים
- כפילויות חשודות
"""

    try:
        res = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2000,
            system="Return only valid JSON. No markdown. No explanation.",
            messages=[{"role": "user", "content": prompt}]
        )

        txt = res.content[0].text.strip()

        if "```" in txt:
            parts = txt.split("```")
            if len(parts) > 1:
                txt = parts[1]

        txt = txt.strip()
        start = txt.find("[")
        end = txt.rfind("]") + 1

        if start != -1 and end != 0:
            txt = txt[start:end]

        return json.loads(txt)

    except Exception:
        return []
