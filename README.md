# Microsoft Copilot Model Guardian

A lightweight, portable background monitor for **Microsoft Copilot on Windows** that continuously ensures your preferred AI model (**GPT 5.6 Think deeper**) remains selected at all times — even when Copilot resets it after a new chat, restart, or session switch.

---

## 📦 Zero Installation — Just Download & Run

| Requirement | Status |
|---|---|
| Admin rights | ❌ Not required |
| Python installation | ❌ Not required |
| Windows version | ✅ Windows 10 / 11 (64-bit) |
| Copilot app | ✅ Microsoft Copilot or Microsoft 365 Copilot |

Just **download the folder** and double-click to start. No setup, no configuration.

---

## 🎯 What It Does

- **Multi-Instance Concurrent Protection** — Monitors and protects **Microsoft Teams Copilot** (both dedicated tab and chat channels) and **Standalone M365 Copilot** simultaneously.
- **Real-Time Action Counter** — Dynamically tracks and displays the total number of enforcement actions directly in the CMD window title and live console output.
- **Continuous Sub-Second Polling** — Checks UI state every `0.2s` with negligible CPU overhead (<0.1%).
- **Instant Auto-Enforcement** — Switches non-target models back to **GPT 5.6 Think deeper** in **~0.29 seconds**.
- **Silent & Non-Intrusive** — Does nothing when your preferred model is already active.
- **Console Lifecycle Binding** — Closing the Command Prompt window automatically stops the guardian.

---

## 🚀 Quick Start

### Option 1 — Run with Console (See Live Logs)
Double-click **`start_guardian.bat`**

A terminal window opens showing real-time status. Logs are also saved to `copilot_guardian.log`. Closing the window immediately stops the guardian.

### Option 2 — Run Silently in Background (No Window)
Double-click **`start_guardian_background.vbs`**

Runs completely invisibly in the background. Logs are written to `copilot_guardian.log`.

### Stop the Guardian
Double-click **`stop_guardian.bat`**

### Auto-Start with Windows Login
Double-click **`install_autostart.bat`** — the guardian will start automatically every time you sign in.

To remove auto-start: double-click **`remove_autostart.bat`**

---

## 📁 Files Included

| File | Description |
|---|---|
| `CopilotModelGuardian.exe` | Standalone executable binary — no Python or dependencies needed |
| `copilot_model_guardian.py` | Full Python source script with UIAutomation engine |
| `start_guardian.bat` | Launch Model Guardian with live status console |
| `start_guardian_background.vbs` | Launch Model Guardian silently in background |
| `stop_guardian.bat` | Stop any running guardian instance |
| `install_autostart.bat` | Register guardian to Windows Startup |
| `remove_autostart.bat` | Remove guardian from Windows Startup |
| `requirements.txt` | Python dependencies (for source usage) |

---

## ⚙️ Advanced CLI Options

```cmd
CopilotModelGuardian.exe --model "GPT 5.6 Think deeper" --interval 0.2 --log-file copilot_guardian.log
```

| Flag | Description | Default |
|---|---|---|
| `--model` | Target model name to enforce | `"GPT 5.6 Think deeper"` |
| `--interval` | Polling interval in seconds | `0.2` |
| `--log-file` | Path to log file | `copilot_guardian.log` |
| `--verbose` | Enable detailed debug logging | `False` |

---

## 🛠️ Building from Source

Requires Python 3.8+ and the dependencies in `requirements.txt`:

```cmd
pip install -r requirements.txt
python copilot_model_guardian.py
```

To rebuild the standalone executable:

```cmd
pip install pyinstaller
pyinstaller --onefile --console --name "CopilotModelGuardian" copilot_model_guardian.py
```

---

## 🔒 Privacy & Security

- **No internet access** — runs entirely locally on your machine.
- **No data collection** — it only reads and clicks UI elements in the Copilot window.
- **Open source** — full source code available in `copilot_model_guardian.py`.

---

## 📋 Known Limitations

- Requires Microsoft Copilot to be **open and signed in** (the guardian waits patiently if Copilot is closed).
- The **GPT 5.6 Think deeper** option requires an active **Microsoft 365 Copilot subscription** that includes GPT model access.
- Built and tested on **Windows 11**. Compatible with Windows 10 (64-bit).

---

## 👤 Author & Contributions

Created and maintained by **Dmitriy Yevseyev** ([@DYevseyev](https://github.com/DYevseyev)).

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://github.com/DYevseyev/copilot-model-guardian/issues).

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

