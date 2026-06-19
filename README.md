# citationHop

> Select a citation anywhere on macOS. Press a hotkey. Get the paper.

`citationHop` is a tiny macOS menu-bar app. Select any chunk of text that
looks like a literature reference (APA / MLA / Chicago / BibTeX / RIS) and
press the global hotkey (default **Cmd + Shift + L**). It will:

1. Extract a DOI directly from the text if one is present
2. Otherwise call the Crossref API to look it up
3. **Open the paper in your default browser** (`https://doi.org/<doi>`)
4. **Copy the bare DOI to your clipboard**
5. If no DOI can be found, open a **Google Scholar** search for the title

No GUI, no dock icon, no friction. A 📎 pin sits in your menu bar.

---

## Installation

```bash
# 1. Create a clean virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install
pip install -r requirements.txt

# 3. Run
python -m citation_hop
```

A 📎 appears in the menu bar. That's it.

## One-time macOS permission setup

The hotkey needs two permissions; macOS will pop up the prompts the
first time you press the hotkey.

| Permission | Why | Where to grant |
|---|---|---|
| **Accessibility** | `pynput` simulates `Cmd+C` to read the current selection | System Settings → Privacy & Security → Accessibility |
| **Automation → System Events** | Fallback `AppleScript` keystroke for apps that ignore the synthetic `Cmd+C` | System Settings → Privacy & Security → Automation |

If the Accessibility prompt never shows up, open
**System Settings → Privacy & Security → Accessibility** manually and
toggle the entry for the Terminal / Python that ran the app.

## Usage

1. Select a citation in any app (PDF reader, browser, Word, Slack, …).
2. Press **Cmd + Shift + L**.
3. The paper opens in your browser; the DOI is in your clipboard.

To change the hotkey, click the 📎 in the menu bar → **Hotkey: …** →
enter a new one in `pynput` syntax (e.g. `cmd+alt+d`, `ctrl+shift+x`).

## Supported input formats

| Format | How DOI is found |
|---|---|
| Plain text with embedded DOI | regex on `10.xxxx/...` |
| `https://doi.org/...` URL | regex on the URL |
| BibTeX (`@article{...}`) | `doi = {…}` field |
| RIS (`TY  - JOUR` …) | `DO  - …` field |
| APA / MLA / Chicago (no DOI) | Crossref bibliographic search, with title-similarity threshold (default 0.85) |
| Nothing matched | Google Scholar search by title + first author + year |

## Configuration

Stored at `~/Library/Application Support/citationHop/config.json`. The
app creates it with sensible defaults on first run. You can edit it
directly, or use the menu's **Open config file** item.

```json
{
  "hotkey": "cmd+shift+l",
  "fallback_engine": "scholar",
  "mailto": "syz@shiyangzheng.top",
  "similarity_threshold": 0.85
}
```

* `mailto` — Crossref's polite pool key. If you fork citationHop,
  change this to your own address.
* `similarity_threshold` — `0.0` (loose) … `1.0` (exact match). Lower
  values give more results but more false positives.

## Running the tests

```bash
pytest -v
```

## Limitations

* **macOS only.** `pynput` global hotkeys + AppleScript fallback + the
  rumps menu bar are all macOS-specific. Linux/Windows ports are
  possible but out of scope for v1.
* **No offline mode.** Crossref lookup requires internet. The text
  never leaves your machine apart from the Crossref request itself.
* **Some apps block synthetic Cmd+C.** Sandboxed apps (e.g. some PDF
  readers in the Mac App Store) won't respond to the simulated
  shortcut. The AppleScript fallback covers most cases; if neither
  works, copy the citation manually and it will land on the clipboard
  — the hotkey will then pick it up.

## License

MIT.
