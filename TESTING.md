# Testing citationHop on Windows

This is a manual end-to-end test plan for verifying the v1.1 cross-platform refactor on Windows.  Run every step on a real Windows 10/11 box (not WSL, not a VM remote — pynput's global hotkey needs the actual Windows event loop).

## 0. Transfer the code

The cleanest way is to clone the GitHub repo (push it first from macOS if you haven't).  Alternative: copy the project folder via OneDrive, USB, or `scp`.

**Recommended: clone**

```powershell
cd $HOME\Documents
git clone https://github.com/ShiyangZheng/citation-hop
cd citation-hop
```

**Or: copy the folder**

The project root must contain `pyproject.toml` and `src/citation_hop/`.  Don't forget hidden files: `.gitignore`, `.github/`, `assets/`.

## 1. One-shot setup (PowerShell)

From the project root, run the helper script:

```powershell
.\scripts\windows_setup.ps1
```

This will:
1. Verify Python 3.10+ is installed
2. Create `.venv` in the project root
3. `pip install -e ".[dev]"`
4. Run `scripts/smoke_test.py` (headless — no GUI required)
5. Run the full `pytest` suite (58 tests)
6. Print next-step instructions

**If the smoke test fails**, do **not** launch the GUI — debug first.  The smoke test exercises every cross-platform code path except the actual tray icon and dialogs.

**If PowerShell blocks the script** with "running scripts is disabled on this system":

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

## 2. Launch the GUI

```powershell
.\.venv\Scripts\Activate.ps1
python -m citation_hop
```

You should see a small icon appear in the system tray (notification area).  Right-click it for the menu.

### If you don't see the tray icon

- Click the **^** (show hidden icons) arrow in the taskbar — pystray defaults to the hidden tray
- If still not visible, the process probably crashed; check `python` output for tracebacks
- Try `python -m citation_hop --verbose` (if you implemented a verbose flag) or run without the venv to see import errors

## 3. Manual test checklist

Tick each item, note any failure with screenshot + exact text.

### A. Tray icon & menu

- [ ] Tray icon appears within 5 s of launch
- [ ] Right-click → menu shows: **Hotkey: Ctrl+Shift+D**, **Modify hotkey**, **Search engines** submenu, **Open config file**, **Quit**
- [ ] Clicking **Search engines** opens a submenu listing all 15 engines with ✓ checkmarks on the 3 default-enabled ones (Crossref, doi.org, Google Scholar)
- [ ] Toggling an engine's checkmark saves immediately (verify by re-opening the menu)
- [ ] **Open config file** opens the JSON config in your default editor
- [ ] **Quit** cleanly exits (no zombie python.exe in Task Manager)

### B. Modify hotkey dialog

- [ ] Click **Modify hotkey** → a native Windows dialog appears (Win32 MessageBox)
- [ ] Default text in the message is the current hotkey (e.g. "Current hotkey: Ctrl+Shift+D")
- [ ] Click **OK** → dialog closes, no error
- [ ] Repeat and click **Cancel** → dialog closes, no error (no traceback in stdout)

### C. Hotkey → citation lookup (the core flow)

Open a browser/PDF reader/Word and select each of the following texts.  After each, press **Ctrl+Shift+D** and verify the right URL opens in your default browser.

- [ ] Plain DOI: `10.1037/0003-066X.59.1.29` → opens `https://doi.org/10.1037/0003-066X.59.1.29`
- [ ] DOI URL: `https://doi.org/10.1037/0003-066X.59.1.29` → same
- [ ] BibTeX entry: a full `@article{...}` block with `doi = {...}` field → opens the DOI URL
- [ ] RIS entry: `TY  - JOUR\nER  - \n...DO  - 10.xxxx/...` → opens the DOI URL
- [ ] Plain citation (no DOI): `Brown, K. W., & Ryan, R. M. (2003). The benefits of being present. Journal of Personality, 71(3), 561-581.` → opens Google Scholar search

### D. Engine toggle behaviour

- [ ] Open the menu, disable **doi.org**, enable **Crossref** (it's on by default — verify the order)
- [ ] Select `10.1037/0003-066X.59.1.29` → hotkey → opens Crossref URL (not doi.org)
- [ ] Disable all `doi_url` engines → select a DOI → hotkey → falls back to a search engine (Google Scholar)
- [ ] Enable **arXiv** (search engine) → select a non-citation text containing "arXiv" → hotkey → opens arXiv search (verify the URL contains `arxiv.org/find` or `arxiv.org/search`)

### E. Config file customisation

- [ ] Open the config file (menu → **Open config file**)
- [ ] Add a new engine entry to the `engines` list, e.g.:
    ```json
    {
      "id": "my_library",
      "name": "My University Library",
      "stage": "search_url",
      "enabled": true,
      "order": 0,
      "url_template": "https://library.example.edu/search?q={query}"
    }
    ```
- [ ] Save the file
- [ ] Reopen the menu → **My University Library** appears under **Search engines** with a checkmark
- [ ] Select a citation without DOI → hotkey → opens `library.example.edu/search?q=...`

### F. Persistence

- [ ] Toggle some engines, change the hotkey via dialog, quit
- [ ] Relaunch → settings persisted (hotkey, engine toggles)

### G. Status notifications

- [ ] Disable the network (or point mailto at an invalid value) → trigger a lookup → a Windows toast notification appears (bottom-right corner) with an error / fallback message
- [ ] Click the toast → does NOT need to do anything (we don't implement action buttons)

### H. Edge cases

- [ ] Select empty text → press hotkey → no crash, no notification, no browser opens
- [ ] Select non-citation text (e.g. a paragraph from a novel) → no crash
- [ ] Trigger hotkey 20 times in quick succession → all 20 URLs open, no hang
- [ ] Press hotkey while another app has focus (e.g. fullscreen game running as admin) → may not register; this is a Windows pynput limitation, not a bug

## 4. Known Windows-specific quirks

These are **expected** behaviour — not bugs.

| Symptom | Cause | Workaround |
| --- | --- | --- |
| Hotkey doesn't fire in apps running as **Administrator** | pynput runs as a non-elevated process; Windows blocks hooks into elevated apps | Run citationHop as Administrator, OR don't run target apps as Administrator |
| First notification toast never appears | `plyer` on Windows uses `win10toast` which is sometimes blocked on first run | Click "Allow notifications" in the Windows notification settings panel |
| Tray icon is hidden by default | Windows hides new tray icons behind the `^` arrow | Drag the icon out of the hidden area, or use "Always show all icons" in taskbar settings |
| Some Unicode characters in dialogs render as `?` | Missing font in the headless dialog code path | Set a UTF-8 codepage: `chcp 65001` before launching |

## 5. Reporting issues

If something fails, capture:
1. **What you did** (which step in this checklist)
2. **What you expected**
3. **What happened** (screenshot, exact error text)
4. **Output of** `python scripts/smoke_test.py` and `python -m pytest -q`
5. **Windows version**: `winver`
6. **Python version**: `python --version`

Open an issue at https://github.com/ShiyangZheng/citation-hop/issues

---

## Cross-platform test matrix (automated CI)

`citationHop` is **CI-tested on every commit** across 9 OS × Python
combinations — the same `tests/` suite and the same `scripts/smoke_test.py`
that this Windows checklist runs locally is what GitHub Actions executes
on all three operating systems:

| OS | Python 3.11 | Python 3.12 | Python 3.13 |
|---|---|---|---|
| `ubuntu-latest` | ✅ | ✅ | ✅ |
| `macos-latest` | ✅ | ✅ | ✅ |
| `windows-latest` | ✅ | ✅ | ✅ |

So if this checklist passes on Windows, the macOS and Linux paths are
already covered by the CI green check on the same commit. See
`.github/workflows/test.yml` for the full matrix.
