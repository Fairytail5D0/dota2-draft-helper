import sys
from PySide6.QtCore import Qt, QTimer, QStringListModel, QThread, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QCheckBox, QPushButton, QScrollArea, QFrame, QCompleter,
    QSpinBox, QButtonGroup, QSlider,
)

from engine import DraftEngine, POSITIONS, POS_SHORT, BRACKETS

BG="#0e1116"; PANEL="#161b22"; PANEL2="#1c2230"; BORDER="#2a3140"
TEXT="#e6edf3"; MUTED="#8b949e"; ENEMY="#ff6b6b"; ALLY="#4dd2a0"
COUNTER="#ffa94d"; SYNERGY="#6ea8fe"; FLEX="#c08cff"; ACCENT="#d64545"
POS_COLORS={"POSITION_1":"#f0a020","POSITION_2":"#e05050","POSITION_3":"#40c060",
            "POSITION_4":"#4090e0","POSITION_5":"#a070e0"}

SEARCH_QSS=(f"QLineEdit{{background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};"
            f"border-radius:6px; padding:7px 10px; font-size:13px;}}")
COMPLETER_QSS=(f"QListView{{background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};"
               f"outline:none; font-size:13px;}}"
               f"QListView::item{{padding:5px 8px;}}"
               f"QListView::item:selected{{background:{ACCENT}; color:white;}}")


class HeroSearch(QLineEdit):
    def __init__(self, placeholder, on_pick, engine):
        super().__init__()
        self.engine = engine
        self.on_pick = on_pick
        self.setPlaceholderText(placeholder)
        self.setStyleSheet(SEARCH_QSS)

        self._model = QStringListModel(self)
        self.completer = QCompleter(self._model, self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.completer.popup().setStyleSheet(COMPLETER_QSS)
        self.setCompleter(self.completer)

        self._all = sorted(h["localized_name"] for h in engine.heroes.values())
        self._model.setStringList(self._all)

        self.textEdited.connect(self._on_edit)
        self.completer.activated.connect(self._on_activated)

    def _on_edit(self, text):
        q = text.strip().lower()
        if not q:
            self._model.setStringList(self._all); return
        sugg = self.engine.suggest(text, limit=12)
        names = [n for n, _ in sugg]
        self._model.setStringList(names or self._all)

    def _on_activated(self, name):
        QTimer.singleShot(0, lambda: self._commit(name))

    def keyPressEvent(self, e: QKeyEvent):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not self.completer.popup().isVisible():
            self._commit(self.text()); return
        super().keyPressEvent(e)

    def _commit(self, text):
        text = (text or "").strip()
        if not text: return
        try:
            hid = self.engine.resolve(text)
            self.on_pick(hid); self.clear()
            self._model.setStringList(self._all)
        except ValueError:
            self.setStyleSheet(SEARCH_QSS + f"QLineEdit{{border:1px solid {ENEMY};}}")
            QTimer.singleShot(600, lambda: self.setStyleSheet(SEARCH_QSS))


class PickChip(QFrame):
    def __init__(self, name, color, on_remove):
        super().__init__()
        self.setStyleSheet(f"background:{PANEL2}; border:1px solid {color}; border-radius:14px;")
        lay=QHBoxLayout(self); lay.setContentsMargins(10,3,6,3); lay.setSpacing(6)
        lbl=QLabel(name); lbl.setStyleSheet(f"color:{TEXT}; font-size:12px; border:none;")
        x=QPushButton("✕"); x.setCursor(Qt.PointingHandCursor); x.setFixedSize(18,18)
        x.setStyleSheet(f"color:{MUTED}; background:transparent; border:none; font-size:12px;")
        x.clicked.connect(on_remove)
        lay.addWidget(lbl); lay.addWidget(x)


class ScannerThread(QThread):
    enemies_changed = Signal(list)
    status = Signal(str)

    CONFIRM = 2   
    MISS = 3      

    def __init__(self, interval=0.6, swap=False):
        super().__init__()
        self.interval = interval
        self.swap = swap
        self._running = False
        self._rec = None
        self._seen = {}
        self._misses = {}
        self._committed = set()
        self._last_emitted = None

    def run(self):
        try:
            from recognize import Recognizer
            self._rec = Recognizer()
            self._rec.set_swap(self.swap)
        except Exception as e:
            self.status.emit(f"Auto disabled: {e}")
            return
        self._running = True
        self.status.emit("Auto-reading enabled")
        while self._running:
            try:
                found = self._rec.read_enemies(None, "enemy") 
                ids_now = {str(hid) for hid, _, _ in found}
                self._update(ids_now)
            except Exception as e:
                self.status.emit(f"scan error: {e}")
            self.msleep(int(self.interval * 1000))

    def _update(self, ids_now):
        for hid in ids_now:
            self._seen[hid] = self._seen.get(hid, 0) + 1
            self._misses[hid] = 0
            if self._seen[hid] >= self.CONFIRM:
                self._committed.add(hid)
        for hid in list(self._committed):
            if hid not in ids_now:
                self._misses[hid] = self._misses.get(hid, 0) + 1
                self._seen[hid] = 0
                if self._misses[hid] >= self.MISS:
                    self._committed.discard(hid)
        snapshot = tuple(sorted(self._committed))
        if snapshot != self._last_emitted:
            self._last_emitted = snapshot
            self.enemies_changed.emit(list(snapshot))

    def stop(self):
        self._running = False
        self.wait(1500)


class DraftApp(QWidget):
    def __init__(self):
        super().__init__()
        self.engine = DraftEngine()
        self.enemy=[]; self.mine=[]
        self.bracket="DIVINE_IMMORTAL"
        self.role=None
        self.axis="counter"
        self.top_n=5
        self.k=300           
        self.scanner=None    
        self.auto_on=False
        self.swap_sides=self._load_swap()   
        self.setWindowTitle("Dota Draft Helper")
        self.resize(1000,720)
        self.setStyleSheet(f"background:{BG};")
        self._build(); self._refresh()

    def _build(self):
        root=QVBoxLayout(self); root.setContentsMargins(16,14,16,14); root.setSpacing(10)

        header=QHBoxLayout()
        title=QLabel("DRAFT HELPER")
        title.setStyleSheet(f"color:{TEXT}; font-size:16px; font-weight:bold; letter-spacing:2px;")
        header.addWidget(title); header.addStretch()
        header.addWidget(self._muted("Rank:"))
        self.bracket_box=QComboBox()
        for b in BRACKETS: self.bracket_box.addItem(b.replace("_"," / ").title(), b)
        self.bracket_box.setCurrentText("Divine / Immortal")
        self.bracket_box.currentIndexChanged.connect(self._on_bracket)
        self.bracket_box.setStyleSheet(self._combo_qss()); header.addWidget(self.bracket_box)
        self.aot=QCheckBox("Always on top"); self.aot.setStyleSheet(f"color:{MUTED}; font-size:12px;")
        self.aot.stateChanged.connect(self._toggle_aot); header.addWidget(self.aot)
        self.auto_btn=QPushButton("⦿ Auto-read"); self.auto_btn.setCheckable(True)
        self.auto_btn.setCursor(Qt.PointingHandCursor)
        self.auto_btn.clicked.connect(self._toggle_auto)
        self.auto_btn.setStyleSheet(self._axis_qss(ALLY))
        header.addWidget(self.auto_btn)
        self.swap_btn=QPushButton("⇄ Sides"); self.swap_btn.setCheckable(True)
        self.swap_btn.setChecked(self.swap_sides); self.swap_btn.setCursor(Qt.PointingHandCursor)
        self.swap_btn.clicked.connect(self._toggle_swap)
        self.swap_btn.setStyleSheet(self._axis_qss(ENEMY))
        header.addWidget(self.swap_btn)
        root.addLayout(header)

        self.status_lbl=QLabel("")
        self.status_lbl.setStyleSheet(f"color:{MUTED}; font-size:11px;")
        root.addWidget(self.status_lbl)

        inputs=QHBoxLayout(); inputs.setSpacing(12)
        ecol=QVBoxLayout(); ecol.addWidget(self._sect("ENEMY PICKS",ENEMY))
        self.enemy_search=HeroSearch("enemy... (type ph -> pick from the list)",self.add_enemy,self.engine)
        ecol.addWidget(self.enemy_search)
        self.enemy_chips=QHBoxLayout(); self.enemy_chips.setSpacing(6); self.enemy_chips.addStretch()
        ew=QWidget(); ew.setLayout(self.enemy_chips); ecol.addWidget(ew); inputs.addLayout(ecol)

        acol=QVBoxLayout(); acol.addWidget(self._sect("OUR PICKS",ALLY))
        self.ally_search=HeroSearch("ally... (for synergy)",self.add_ally,self.engine)
        acol.addWidget(self.ally_search)
        self.ally_chips=QHBoxLayout(); self.ally_chips.setSpacing(6); self.ally_chips.addStretch()
        aw=QWidget(); aw.setLayout(self.ally_chips); acol.addWidget(aw); inputs.addLayout(acol)
        root.addLayout(inputs)

        ctrl=QHBoxLayout(); ctrl.setSpacing(8)
        ctrl.addWidget(self._muted("My role:"))
        self.role_box=QComboBox()
        self.role_box.addItem("All positions", None)
        for p in POSITIONS: self.role_box.addItem(POS_SHORT[p], p)
        self.role_box.currentIndexChanged.connect(self._on_role)
        self.role_box.setStyleSheet(self._combo_qss()); ctrl.addWidget(self.role_box)

        ctrl.addSpacing(14); ctrl.addWidget(self._muted("Show:"))
        self.axis_group=QButtonGroup(self)
        for key,label,color in [("counter","Counters",COUNTER),
                                 ("synergy","Synergy",SYNERGY),
                                 ("flexible","Flexible",FLEX)]:
            b=QPushButton(label); b.setCheckable(True); b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key=="counter")
            b.clicked.connect(lambda _=False,k=key:self._set_axis(k))
            b.setStyleSheet(self._axis_qss(color))
            self.axis_group.addButton(b); ctrl.addWidget(b)

        ctrl.addSpacing(14); ctrl.addWidget(self._muted("Count:"))
        self.count=QSpinBox(); self.count.setRange(3,20); self.count.setValue(5)
        self.count.valueChanged.connect(self._on_count)
        self.count.setStyleSheet(
            f"QSpinBox{{background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};"
            f"border-radius:6px; padding:3px 6px; font-size:12px;}}")
        ctrl.addWidget(self.count)

        ctrl.addSpacing(14)
        self.k_label=self._muted(f"Sample trust: {self.k}")
        ctrl.addWidget(self.k_label)
        self.k_slider=QSlider(Qt.Horizontal)
        self.k_slider.setRange(0, 1500); self.k_slider.setValue(self.k)
        self.k_slider.setFixedWidth(120)
        self.k_slider.valueChanged.connect(self._on_k)
        self.k_slider.setStyleSheet(
            f"QSlider::groove:horizontal{{height:4px; background:{BORDER}; border-radius:2px;}}"
            f"QSlider::handle:horizontal{{width:14px; height:14px; margin:-6px 0;"
            f"background:{ACCENT}; border-radius:7px;}}"
            f"QSlider::sub-page:horizontal{{background:{ACCENT}; border-radius:2px;}}")
        ctrl.addWidget(self.k_slider)

        ctrl.addStretch()
        clear=QPushButton("Clear"); clear.setCursor(Qt.PointingHandCursor)
        clear.clicked.connect(self._clear)
        clear.setStyleSheet(f"color:{MUTED}; background:{PANEL}; border:1px solid {BORDER};"
                            f"border-radius:6px; padding:5px 12px; font-size:12px;")
        ctrl.addWidget(clear)
        root.addLayout(ctrl)

        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("border:none;")
        self.host=QWidget(); self.host_l=QVBoxLayout(self.host)
        self.host_l.setContentsMargins(0,0,0,0); self.host_l.setSpacing(10)
        self.scroll.setWidget(self.host); root.addWidget(self.scroll,1)

    def _muted(self,t): l=QLabel(t); l.setStyleSheet(f"color:{MUTED}; font-size:12px;"); return l
    def _sect(self,t,c): l=QLabel(t); l.setStyleSheet(f"color:{c}; font-size:11px; font-weight:bold; letter-spacing:2px;"); return l
    def _combo_qss(self):
        return (f"QComboBox{{background:{PANEL2}; color:{TEXT}; border:1px solid {BORDER};"
                f"border-radius:6px; padding:4px 8px; font-size:12px;}}"
                f"QComboBox QAbstractItemView{{background:{PANEL2}; color:{TEXT};"
                f"selection-background-color:{ACCENT};}}")
    def _axis_qss(self,color):
        return (f"QPushButton{{color:{MUTED}; background:{PANEL}; border:1px solid {BORDER};"
                f"border-radius:6px; padding:5px 12px; font-size:12px;}}"
                f"QPushButton:checked{{color:white; background:{color}; border:1px solid {color};"
                f"font-weight:bold;}}")

    def keyPressEvent(self,e:QKeyEvent):
        if e.text() and e.text().isprintable() and \
           not self.enemy_search.hasFocus() and not self.ally_search.hasFocus():
            self.enemy_search.setFocus()
            self.enemy_search.setText(self.enemy_search.text()+e.text())
            self.enemy_search.textEdited.emit(self.enemy_search.text())
        else:
            super().keyPressEvent(e)

    def add_enemy(self,hid):
        if self.auto_on: return  
        if hid not in self.enemy and hid not in self.mine and len(self.enemy)<5:
            self.enemy.append(hid); self._refresh()
    def add_ally(self,hid):
        if hid not in self.mine and hid not in self.enemy and len(self.mine)<5:
            self.mine.append(hid); self._refresh()
    def remove_enemy(self,hid):
        if self.auto_on: return
        self.enemy=[h for h in self.enemy if h!=hid]; self._refresh()
    def remove_ally(self,hid): self.mine=[h for h in self.mine if h!=hid]; self._refresh()
    def _clear(self):
        if self.auto_on: return
        self.enemy=[]; self.mine=[]; self._refresh()
    def _on_bracket(self): self.bracket=self.bracket_box.currentData(); self._refresh()
    def _on_role(self): self.role=self.role_box.currentData(); self._refresh()
    def _on_count(self,v): self.top_n=v; self._refresh()
    def _on_k(self,v):
        self.k=v; self.k_label.setText(f"Sample trust: {v}"); self._refresh()
    def _set_axis(self,k): self.axis=k; self._refresh()
    def _toggle_aot(self):
        self.setWindowFlag(Qt.WindowStaysOnTopHint,self.aot.isChecked()); self.show()

    def _load_swap(self):
        try:
            import json
            from pathlib import Path
            p = Path(__file__).parent / "data" / "regions.json"
            if p.exists():
                return bool(json.load(open(p, encoding="utf-8")).get("swap", False))
        except Exception:
            pass
        return False

    def _toggle_swap(self):
        self.swap_sides = self.swap_btn.isChecked()
        try:
            import json
            from pathlib import Path
            p = Path(__file__).parent / "data" / "regions.json"
            data = json.load(open(p, encoding="utf-8"))
            data["swap"] = self.swap_sides
            json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        except Exception:
            pass
        if self.auto_on:
            self._restart_scanner()
        self.status_lbl.setText("Sides: " + ("swapped (enemies = right row)"
                                                if self.swap_sides else "default (enemies = left row)"))

    def _restart_scanner(self):
        if self.scanner:
            self.scanner.stop(); self.scanner=None
        self.scanner=ScannerThread(interval=0.6, swap=self.swap_sides)
        self.scanner.enemies_changed.connect(self._auto_enemies)
        self.scanner.status.connect(self.status_lbl.setText)
        self.scanner.start()

    def _toggle_auto(self):
        if self.auto_btn.isChecked():
            self.auto_on=True
            self.enemy_search.setEnabled(False)
            self._restart_scanner()
        else:
            self.auto_on=False
            self.enemy_search.setEnabled(True)
            self.status_lbl.setText("")
            if self.scanner:
                self.scanner.stop(); self.scanner=None

    def _auto_enemies(self, ids):
        self.enemy=[h for h in ids][:5]
        self._refresh()

    def closeEvent(self, e):
        if self.scanner:
            self.scanner.stop()
        super().closeEvent(e)

    def _rebuild_chips(self,layout,ids,color,remover):
        while layout.count()>1:
            it=layout.takeAt(0)
            if it.widget(): it.widget().deleteLater()
        for hid in ids:
            layout.insertWidget(layout.count()-1,
                                PickChip(self.engine.name(hid),color,
                                         lambda _=False,h=hid:remover(h)))

    def _refresh(self):
        self._rebuild_chips(self.enemy_chips,self.enemy,ENEMY,self.remove_enemy)
        self._rebuild_chips(self.ally_chips,self.mine,ALLY,self.remove_ally)
        while self.host_l.count():
            it=self.host_l.takeAt(0)
            if it.widget(): it.widget().deleteLater()

        if self.axis=="synergy" and not self.mine:
            self._hint("Add at least one of your own heroes — synergy will appear here."); return
        if not self.enemy and self.axis!="synergy":
            self._hint("Add enemy picks.\nTip: just start typing a hero name."); return

        positions=[self.role] if self.role else POSITIONS
        recs=self.engine.recommend(self.enemy,my_ids=self.mine,bracket=self.bracket,
                                   open_positions=positions,top_n=self.top_n,k=self.k)
        for pos in positions:
            self.host_l.addWidget(self._card(pos,recs[pos]))
        self.host_l.addStretch()

    def _hint(self,text):
        l=QLabel(text); l.setAlignment(Qt.AlignCenter)
        l.setStyleSheet(f"color:{MUTED}; font-size:13px;")
        self.host_l.addWidget(l); self.host_l.addStretch()

    def _card(self,pos,data):
        color={"counter":COUNTER,"synergy":SYNERGY,"flexible":FLEX}[self.axis]
        rows=data[self.axis]
        card=QFrame(); card.setStyleSheet(f"background:{PANEL}; border:1px solid {BORDER}; border-radius:10px;")
        lay=QVBoxLayout(card); lay.setContentsMargins(14,10,14,12); lay.setSpacing(6)
        head=QLabel(f"●  {POS_SHORT[pos]}")
        head.setStyleSheet(f"color:{POS_COLORS[pos]}; font-size:13px; font-weight:bold; letter-spacing:1px; border:none;")
        lay.addWidget(head)
        if not rows:
            e=QLabel("— no candidates —"); e.setStyleSheet(f"color:{MUTED}; border:none;")
            lay.addWidget(e); return card
        for i,r in enumerate(rows):
            lay.addLayout(self._row(i,r,color))
        return card

    def _row(self,i,r,color):
        box=QVBoxLayout(); box.setSpacing(1)
        line=QHBoxLayout(); line.setSpacing(8)
        rank=QLabel(f"{i+1}"); rank.setFixedWidth(16)
        rank.setStyleSheet(f"color:{MUTED}; font-size:11px; border:none;")
        name=QLabel(r["name"])
        name.setStyleSheet(f"color:{TEXT}; font-size:13px; font-weight:{'bold' if i==0 else 'normal'}; border:none;")
        score=QLabel(f"{r['score']:+.1f}")
        score.setStyleSheet(f"color:{color}; font-size:13px; font-weight:bold; border:none;")
        score.setAlignment(Qt.AlignRight)
        line.addWidget(rank); line.addWidget(name,1); line.addWidget(score)
        box.addLayout(line)
        bits=[]
        if r.get("reasons"):
            tag = "with: " if self.axis=="synergy" else "vs: "
            bits.append(tag+", ".join(f"{n} {v:+.0f} ({g} games)" for n,v,g in r["reasons"]))
        if self.axis=="flexible" and r.get("risk"):
            bits.append("risk: "+", ".join(n for n,_ in r["risk"]))
        if bits:
            w=QLabel("     "+"   |   ".join(bits))
            w.setStyleSheet(f"color:{MUTED}; font-size:10px; border:none;")
            box.addWidget(w)
        return box


def main():
    app=QApplication(sys.argv)
    f=QFont("Segoe UI"); f.setPointSize(10); app.setFont(f)
    w=DraftApp(); w.show()
    sys.exit(app.exec())


if __name__=="__main__":
    main()
