"""Dark theme stylesheet."""

BG, PANEL, BUBBLE_AI, ACCENT, TEXT, MUTED = "#14161b", "#1c1f27", "#232733", "#6c8cff", "#e6e8ee", "#8a90a2"
STATE_COLORS = {"idle": "#6b7280", "listening": "#34d399", "thinking": "#fbbf24", "speaking": "#6c8cff"}

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 13px; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: {BG}; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #333847; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
#panel {{ background: {PANEL}; border-radius: 12px; }}
#panel QLabel {{ background: transparent; color: {MUTED}; }}
QLabel#bubbleUser {{ background: {ACCENT}; color: #0b0d12; border-radius: 14px; padding: 9px 13px; }}
QLabel#bubbleAI {{ background: {BUBBLE_AI}; border-radius: 14px; padding: 9px 13px; }}
QLabel#bubbleNote {{ background: transparent; color: {MUTED}; font-size: 12px; }}
QLabel#bubbleErr {{ background: #3a1f24; color: #ffb4b4; border-radius: 10px; padding: 7px 11px; }}
QLabel#partial {{ color: {MUTED}; font-style: italic; background: transparent; }}
QLabel#small {{ color: {MUTED}; font-size: 11px; background: transparent; }}
QLineEdit {{ background: {PANEL}; border: 1px solid #2a2f3c; border-radius: 18px; padding: 8px 14px; }}
QLineEdit:focus {{ border-color: {ACCENT}; }}
QPushButton {{ background: {PANEL}; border: 1px solid #2a2f3c; border-radius: 16px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:checked {{ background: {ACCENT}; color: #0b0d12; border-color: {ACCENT}; }}
QPushButton#talk {{ background: {ACCENT}; color: #0b0d12; border: none; font-weight: 600; }}
QPushButton#icon {{ padding: 6px 10px; border-radius: 14px; }}
QComboBox {{ background: {BG}; border: 1px solid #2a2f3c; border-radius: 8px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {ACCENT}; selection-color: #0b0d12; }}
QProgressBar {{ background: #232733; border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: #34d399; border-radius: 2px; }}
QSlider::groove:horizontal {{ background: #2a2f3c; height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid #2a2f3c; }}
"""
