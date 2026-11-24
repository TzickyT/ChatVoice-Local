"""Turn replacements.txt into word substitutions for ChatVoice Local.

Safest way:
  1. In ChatVoice Local: Blocked users page -> Export backup...  (save as ChatVoice_Local_Backup.json next to this file)
  2. Run:  python add_replacements_to_backup.py
  3. In ChatVoice Local: Import backup... -> choose ChatVoice_Local_Backup_with_replacements.json

Your channels, voices and blocked users are kept; only the "Replace before speaking" list is replaced by replacements.txt.
Without an exported backup it writes a minimal one, but then channel names may be reset, so export one first.
"""
import json, os, sys

here = os.path.dirname(os.path.abspath(__file__))
src = os.path.join(here, "replacements.txt")
exported = sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "ChatVoice_Local_Backup.json")
out = os.path.join(here, "ChatVoice_Local_Backup_with_replacements.json")

subs = []
with open(src, encoding="utf-8-sig") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        word, _, spoken = line.partition("=")
        word, spoken = word.strip(), spoken.strip()
        if word:
            subs.append({"word": word, "replacement": spoken})

if os.path.exists(exported):
    with open(exported, encoding="utf-8") as f:
        backup = json.load(f)
    print("Using your exported backup:", exported)
else:
    backup = {"format": "ChatVoice Local backup", "version": 3, "settings": {}, "blocked_users": [], "voice_presets": {}}
    print("No exported backup found - writing a minimal one.")
backup.setdefault("settings", {})["word_substitutions"] = subs
with open(out, "w", encoding="utf-8") as f:
    json.dump(backup, f, ensure_ascii=False, indent=2)
print(f"Wrote {len(subs)} replacements to {out}")
