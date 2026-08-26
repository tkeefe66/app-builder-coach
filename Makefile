# Makefile
sweep:
	.venv/bin/python -m src.sweep

test:
	.venv/bin/python -m pytest tests/ -v

install-schedule:
	cp launchd/com.tomkeefe.app-builder-coach.plist ~/Library/LaunchAgents/
	launchctl unload ~/Library/LaunchAgents/com.tomkeefe.app-builder-coach.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.tomkeefe.app-builder-coach.plist
.PHONY: sweep test install-schedule
