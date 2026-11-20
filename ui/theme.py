"""Dark theme stylesheet and palette constants."""

ACCENT = "#FF2D75"      # hot pink (GTA-style)
ACCENT_2 = "#7C4DFF"    # violet
CYAN = "#22D3EE"
GREEN = "#22C55E"
YELLOW = "#FACC15"
RED = "#EF4444"
BG = "#0E0F14"
BG_2 = "#151722"
BG_3 = "#1C1F2E"
BORDER = "#2A2E42"
TEXT = "#E8EAF2"
TEXT_DIM = "#8B90A7"

STYLESHEET = f"""
* {{
    font-family: 'Segoe UI', 'Inter', sans-serif;
    font-size: 13px;
    color: {TEXT};
}}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget#Sidebar {{ background: {BG_2}; border-right: 1px solid {BORDER}; }}
QLabel#Logo {{ font-size: 20px; font-weight: 800; color: {TEXT}; padding: 6px 4px; }}
QLabel#LogoAccent {{ font-size: 20px; font-weight: 800; color: {ACCENT}; }}
QPushButton#NavButton {{
    text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
    background: transparent; color: {TEXT_DIM}; font-size: 14px; font-weight: 600;
}}
QPushButton#NavButton:hover {{ background: {BG_3}; color: {TEXT}; }}
QPushButton#NavButton:checked {{ background: {BG_3}; color: {TEXT}; border-left: 3px solid {ACCENT}; }}

QFrame#Card {{ background: {BG_2}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#CardTitle {{ color: {TEXT_DIM}; font-size: 11px; font-weight: 700; letter-spacing: 1px; }}
QLabel#BigValue {{ font-size: 20px; font-weight: 700; }}
QLabel#H1 {{ font-size: 22px; font-weight: 800; }}
QLabel#Dim {{ color: {TEXT_DIM}; }}
QLabel#Valid {{ color: {GREEN}; }}
QLabel#Invalid {{ color: {RED}; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
    background: {BG_3}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 10px;
    selection-background-color: {ACCENT};
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit#UrlInput {{ font-size: 15px; padding: 11px 14px; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    background: transparent; border: none; width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-bottom: 5px solid {TEXT_DIM}; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid {TEXT_DIM}; }}
QComboBox QAbstractItemView {{ background: {BG_3}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}

QPushButton {{
    background: {BG_3}; border: 1px solid {BORDER}; border-radius: 8px; padding: 8px 16px; font-weight: 600;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:disabled {{ color: #555a70; border-color: #23263a; }}
QPushButton#Primary {{ background: {ACCENT}; border: none; color: white; padding: 11px 26px; font-size: 14px; }}
QPushButton#Primary:hover {{ background: #ff4d8b; }}
QPushButton#Primary:disabled {{ background: #5a2a3d; color: #b7a0aa; }}
QPushButton#Danger {{ background: transparent; border: 1px solid {RED}; color: {RED}; padding: 11px 22px; font-size: 14px; }}
QPushButton#Danger:hover {{ background: {RED}; color: white; }}
QPushButton#Danger:disabled {{ border-color: #3d2a2a; color: #6b4b4b; }}
QPushButton#ModeButton {{
    background: {BG_3}; border: 1px solid {BORDER}; padding: 12px 18px; font-size: 14px; border-radius: 10px;
}}
QPushButton#ModeButton:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); border: none; color: white; }}

QProgressBar {{ background: {BG_3}; border: none; border-radius: 6px; height: 12px; text-align: center; font-size: 11px; }}
QProgressBar::chunk {{ border-radius: 6px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT_2}); }}

QListWidget, QTextEdit, QPlainTextEdit, QTableWidget {{
    background: {BG_3}; border: 1px solid {BORDER}; border-radius: 8px; padding: 4px;
}}
QListWidget::item {{ padding: 6px; border-radius: 6px; }}
QListWidget::item:selected {{ background: #2b2140; }}
QHeaderView::section {{ background: {BG_2}; border: none; padding: 6px; color: {TEXT_DIM}; }}
QScrollArea {{ border: none; background: transparent; }}
QWidget#ScrollBody {{ background: {BG}; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSlider::groove:horizontal {{ height: 6px; background: {BG_3}; border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 3px; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 4px; border: 1px solid {BORDER}; background: {BG_3}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QGroupBox {{ border: 1px solid {BORDER}; border-radius: 10px; margin-top: 16px; padding: 14px 10px 10px 10px; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {TEXT_DIM}; }}
QToolTip {{ background: {BG_3}; border: 1px solid {BORDER}; color: {TEXT}; padding: 6px; }}
"""
