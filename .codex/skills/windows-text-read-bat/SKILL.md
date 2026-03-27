---
name: windows-text-read-bat
description: Use this skill when reading `.md` or `.txt` files on Windows, especially if terminal rendering may corrupt Chinese text or show mojibake. Always read those files through the bundled bat script instead of plain `Get-Content`.
---

# Windows Text Read Bat

When working on Windows, do not read `.md` or `.txt` files with plain `Get-Content` if the content matters.

Use the bundled bat script instead:

```powershell
.codex\skills\windows-text-read-bat\scripts\read_text_utf8.bat <path>
```

## Rules

- For `.md` and `.txt` files on Windows, prefer the bat script over plain shell reads.
- Default to escaped output because it is robust against terminal encoding corruption.
- If you need visually readable output and know the terminal can render UTF-8 correctly, use `raw` mode.
- Treat the script output as the source of truth for text content when shell rendering is suspicious.

## Modes

The script accepts:

```powershell
.codex\skills\windows-text-read-bat\scripts\read_text_utf8.bat <path> [escaped|raw|lines]
```

- `escaped`: default; outputs ASCII-safe unicode escapes
- `raw`: outputs the original text
- `lines`: outputs line-numbered unicode escapes

## Recommended Usage

- Quick safe read:
  ```powershell
  .codex\skills\windows-text-read-bat\scripts\read_text_utf8.bat README.md
  ```
- Line-numbered inspection:
  ```powershell
  .codex\skills\windows-text-read-bat\scripts\read_text_utf8.bat README.md lines
  ```

## Notes

- The script reads files as UTF-8.
- The `escaped` and `lines` modes are intended for reliable inspection when Chinese text appears garbled in PowerShell output.
- This skill is about reading only. Writing should still explicitly use UTF-8 without BOM.
