"""Dark theme stylesheet, with an optional frosted-glass variant (translucent cards over the Windows 11 acrylic/Mica backdrop)."""

BG, PANEL, BUBBLE_AI, ACCENT, TEXT, MUTED = "#14161b", "#1c1f27", "#232733", "#6c8cff", "#e6e8ee", "#8a90a2"
STATE_COLORS = {"idle": "#6b7280", "listening": "#34d399", "thinking": "#fbbf24", "speaking": "#6c8cff"}
ACCENTS = {"Blue": "#6c8cff", "Violet": "#a78bfa", "Teal": "#2dd4bf", "Green": "#4ade80", "Amber": "#fbbf24", "Rose": "#fb7185"}
ICON_FONT = "'Segoe Fluent Icons', 'Segoe MDL2 Assets'"  # Windows icon fonts; the glyph codes are the same in both


def _rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgba(h: str, a: float) -> str:
    r, g, b = _rgb(h)
    return f"rgba({r},{g},{b},{max(0.0, min(1.0, a)):.2f})"


def on_accent(accent: str) -> str:
    """Text colour that stays readable on the accent colour."""
    r, g, b = _rgb(accent)
    return "#0b0d12" if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.55 else "#ffffff"


def window_tint(tint: int) -> float:
    """Alpha of the dark veil painted over the glass: more tint, calmer background."""
    return 0.10 + 0.65 * max(0, min(100, tint)) / 100


def build_style(accent: str = ACCENT, glass: bool = False, tint: int = 70) -> str:
    k = max(0, min(100, tint)) / 100
    ink = on_accent(accent)
    if glass:  # cards are see-through; a higher tint makes them more solid
        base, panel, card = "transparent", rgba(PANEL, 0.30 + 0.55 * k), rgba(PANEL, 0.35 + 0.5 * k)
        ai, field, line = rgba(BUBBLE_AI, 0.45 + 0.5 * k), rgba(BG, 0.25 + 0.5 * k), "rgba(255,255,255,0.10)"
        err = rgba("#3a1f24", 0.6 + 0.35 * k)
        window = ""
    else:
        base, panel, card, ai, field, line, err = BG, PANEL, PANEL, BUBBLE_AI, BG, "#2a2f3c", "#3a1f24"
        window = ""
    return f"""
QWidget {{ background: {base}; color: {TEXT}; font-family: 'Segoe UI Variable', 'Segoe UI'; font-size: 13px; }}
{window}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 0; }}
QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.18); border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
#panel {{ background: {panel}; border: 1px solid {line}; border-radius: 14px; }}
#panel QLabel {{ background: transparent; color: {MUTED}; }}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QLabel#bubbleUser {{ background: {accent}; color: {ink}; border-radius: 14px; padding: 9px 13px; }}
QLabel#bubbleAI {{ background: {ai}; border: 1px solid {line}; border-radius: 14px; padding: 9px 13px; }}
QLabel#bubbleNote {{ background: transparent; color: {MUTED}; font-size: 12px; }}
QLabel#bubbleErr {{ background: {err}; color: #ffb4b4; border-radius: 10px; padding: 7px 11px; }}
QLabel#partial {{ color: {MUTED}; font-style: italic; background: transparent; }}
QLabel#small {{ color: {MUTED}; font-size: 11px; background: transparent; }}
QLabel#pageTitle {{ color: {TEXT}; font-size: 20px; font-weight: 600; background: transparent; }}
QLabel#pageBlurb {{ color: {MUTED}; font-size: 13px; background: transparent; }}
QPushButton#foldout {{ background: transparent; border: none; color: {MUTED}; font-weight: 600; padding: 6px 2px; text-align: left; }}
QPushButton#foldout:hover {{ color: {TEXT}; }}
QPushButton#foldout:checked {{ background: transparent; color: {TEXT}; border: none; }}
QLabel#status {{ font-size: 14px; font-weight: 600; background: transparent; }}
QLineEdit, QPlainTextEdit {{ background: {card}; border: 1px solid {line}; border-radius: 18px; padding: 8px 14px; selection-background-color: {accent}; }}
QPlainTextEdit {{ border-radius: 10px; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {accent}; }}
QPushButton {{ background: {card}; border: 1px solid {line}; border-radius: 16px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {accent}; }}
QPushButton:checked {{ background: {accent}; color: {ink}; border-color: {accent}; }}
QPushButton#talk {{ background: {accent}; color: {ink}; border: none; font-weight: 600; padding: 8px 20px; }}
QPushButton#version {{ background: transparent; border: none; color: {MUTED}; font-size: 11px; padding: 2px 4px; text-align: left; }}
QPushButton#version[update="true"] {{ color: {accent}; font-weight: 600; text-decoration: underline; }}
QPushButton#icon {{ font-family: {ICON_FONT}; font-size: 15px; padding: 0; min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px; border-radius: 17px; }}
QPushButton#swatch {{ padding: 0; min-width: 26px; max-width: 26px; min-height: 26px; max-height: 26px; border-radius: 13px; border: 2px solid transparent; }}
QPushButton#swatch:checked {{ border-color: {TEXT}; }}
QComboBox, QSpinBox, QDoubleSpinBox {{ background: {field}; border: 1px solid {line}; border-radius: 8px; padding: 5px 8px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; selection-background-color: {accent}; selection-color: {ink}; }}
QProgressBar {{ background: rgba(255,255,255,0.08); border: none; border-radius: 2px; }}
QProgressBar::chunk {{ background: #34d399; border-radius: 2px; }}
QSlider::groove:horizontal {{ background: rgba(255,255,255,0.14); height: 4px; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {accent}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QTabWidget::pane {{ border: none; background: transparent; }}
QListWidget {{ background: {field}; border: 1px solid {line}; border-radius: 8px; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ padding: 7px 14px; margin: 1px 0; border-radius: 9px; color: {MUTED}; }}
QListWidget#nav::item:hover {{ color: {TEXT}; background: rgba(255,255,255,0.06); }}
QListWidget#nav::item:selected {{ color: {TEXT}; background: {rgba(accent, 0.25)}; }}
QListWidget#history {{ background: transparent; border: none; outline: none; }}
QListWidget#history::item {{ padding: 8px 10px; margin: 1px 0; border-radius: 9px; color: {TEXT}; }}
QListWidget#history::item:hover {{ background: rgba(255,255,255,0.06); }}
QListWidget#history::item:selected {{ color: {TEXT}; background: {rgba(accent, 0.25)}; }}
QToolTip {{ background: {PANEL}; color: {TEXT}; border: 1px solid {line}; }}
"""


STYLE = build_style()  # opaque: the app-wide base and what every dialog uses
