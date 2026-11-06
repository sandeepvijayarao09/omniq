"""Generate the two professional flowcharts (SVG) used on /workflow and in the README."""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "omniq" / "static"
C = {"ink": "#0f1b2a", "mute": "#5b6878", "line": "#8b98a7", "lane": ["#f7f9fc", "#fbfcfd"],
     "blue": ("#e8f1fb", "#1565c0"), "amber": ("#fff4e0", "#b26b00"), "violet": ("#f1eafb", "#6d3fb8"),
     "red": ("#fdecec", "#c62f35"), "green": ("#e3f6ec", "#00a35c"), "teal": ("#e0f2f1", "#00796b"), "grey": ("#ffffff", "#9aa6b2")}
FONT = "Inter, ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif"


class SVG:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []

    def add(self, s):
        self.parts.append(s)

    def text(self, x, y, s, size=13, weight=600, fill=None, anchor="middle"):
        s = str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill or C["ink"]}" text-anchor="{anchor}" font-family="{FONT}">{s}</text>')

    def lines(self, cx, y0, title, sub, tsize=13.5):
        self.text(cx, y0, title, tsize, 700)
        for i, l in enumerate(sub):
            self.text(cx, y0 + 17 + i * 15, l, 11.5, 500, C["mute"])

    def box(self, x, y, w, h, title, sub=(), col="grey", r=10):
        f, s = C[col]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{f}" stroke="{s}" stroke-width="1.6"/>')
        n = len(sub)
        self.lines(x + w / 2, y + h / 2 - (n * 15) / 2 + 4, title, sub)

    def term(self, x, y, w, h, title, col="teal"):
        f, s = C[col]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h/2}" fill="{s}" stroke="{s}"/>')
        self.text(x + w / 2, y + h / 2 + 5, title, 13, 700, "#fff")

    def diamond(self, cx, cy, w, h, title, sub=(), col="amber"):
        f, s = C[col]
        self.add(f'<polygon points="{cx},{cy-h/2} {cx+w/2},{cy} {cx},{cy+h/2} {cx-w/2},{cy}" fill="{f}" stroke="{s}" stroke-width="1.6"/>')
        n = len(sub)
        self.lines(cx, cy - (n * 15) / 2 + 4, title, sub, 12.5)

    def cyl(self, x, y, w, h, title, sub=(), col="green"):
        f, s = C[col]
        e = 9
        self.add(f'<path d="M{x},{y+e} A{w/2},{e} 0 0 1 {x+w},{y+e} L{x+w},{y+h-e} A{w/2},{e} 0 0 1 {x},{y+h-e} Z" fill="{f}" stroke="{s}" stroke-width="1.6"/>')
        self.add(f'<path d="M{x},{y+e} A{w/2},{e} 0 0 0 {x+w},{y+e}" fill="none" stroke="{s}" stroke-width="1.6"/>')
        n = len(sub)
        self.lines(x + w / 2, y + h / 2 - (n * 15) / 2 + 8, title, sub)

    def arrow(self, pts, label=None, lx=None, ly=None, col=None, dash=False):
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        st = col or C["line"]
        self.add(f'<path d="{d}" fill="none" stroke="{st}" stroke-width="1.8" {"stroke-dasharray=\"6 5\"" if dash else ""} marker-end="url(#ah{"g" if col else ""})"/>')
        if label:
            x = lx if lx is not None else (pts[0][0] + pts[1][0]) / 2
            y = ly if ly is not None else (pts[0][1] + pts[1][1]) / 2 - 6
            wl = 7.2 * len(label) + 12
            self.add(f'<rect x="{x - wl/2}" y="{y - 12}" width="{wl}" height="18" rx="9" fill="#fff" stroke="{st}" stroke-width="1"/>')
            self.text(x, y + 1, label, 11, 700, st)

    def lane(self, y, h, name, i):
        self.add(f'<rect x="0" y="{y}" width="{self.w}" height="{h}" fill="{C["lane"][i % 2]}"/>')
        self.add(f'<line x1="0" y1="{y}" x2="{self.w}" y2="{y}" stroke="#e6ebf0"/>')
        self.add(f'<rect x="0" y="{y}" width="104" height="{h}" fill="#eef2f6"/>')
        for j, part in enumerate(name.split("|")):
            self.text(52, y + h / 2 + 4 - (len(name.split("|")) - 1) * 7 + j * 14, part, 11, 800, C["mute"])

    def render(self):
        defs = (f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{C["line"]}"/></marker>'
                f'<marker id="ahg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M0,0 L10,5 L0,10 z" fill="{C["green"][1]}"/></marker></defs>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="100%" role="img">{defs}'
                + "".join(self.parts) + "</svg>")


def agent_chart():
    s = SVG(1530, 640)
    lanes = [(20, 115, "PEOPLE"), (135, 115, "BEHAVIOUR|SIGNALS"), (250, 115, "IMPROVEMENT|ENGINE"),
             (365, 115, "GATES|& PROOF"), (480, 140, "STORE|& AUDIT")]
    for i, (y, h, n) in enumerate(lanes):
        s.lane(y, h, n, i)
    # nodes
    s.term(124, 52, 236, 46, "Person hands off a job")
    s.box(130, 160, 220, 66, "Behaviour signals", ["rephrase · correct · extend", "regenerate · copy · 👍👎"], "blue")
    s.box(390, 160, 220, 66, "Where it fails", ["failing answers by type", "and playbook version"], "blue")
    s.box(390, 275, 220, 66, "7-day cycle ledger", ["every 7 days · failure spike", "or 'Improve from last 7 days'"], "amber")
    s.diamond(735, 308, 190, 92, "Same pattern", ["from 2+ people?"])
    s.box(640, 44, 190, 62, "Personal preference only", ["applies to that person now"], "grey")
    s.box(880, 275, 210, 66, "Propose ONE change", ["rules · retrieval · workflow", "output template"], "violet")
    s.diamond(985, 422, 176, 92, "Pass every", ["gate + leak test?"], "red")
    s.box(760, 397, 125, 50, "🛑 Refused", ["audited"], "red")
    s.diamond(1180, 422, 176, 92, "Proven better?", ["tests · blind replay"], "red")
    s.box(1095, 280, 170, 56, "↩️ Undone", ["numbers recorded"], "amber")
    s.diamond(1375, 422, 150, 86, "Template or", ["tool change?"], "violet")
    s.box(1290, 280, 185, 56, "🙋 Manager approves", ["IAM harness:approve"], "violet")
    s.cyl(1278, 505, 205, 84, "✅ New playbook version", ["parent · change · evidence", "scores · audit entry"], "green")
    s.diamond(1060, 547, 180, 84, "Live failure rate", ["worse than parent?"], "amber")
    s.box(745, 521, 175, 52, "⏪ Auto-rollback", ["restore parent version"], "red")
    s.term(410, 525, 230, 46, "↺ Next answer uses it", "green")
    # edges
    s.arrow([(240, 98), (240, 160)])
    s.arrow([(360, 75), (500, 75), (500, 160)])
    s.arrow([(240, 226), (240, 308), (390, 308)])
    s.arrow([(500, 226), (500, 275)])
    s.arrow([(610, 308), (640, 308)])
    s.arrow([(735, 262), (735, 106)], "No", 735, 188)
    s.arrow([(830, 308), (880, 308)], "Yes", 855, 300)
    s.arrow([(985, 341), (985, 376)])
    s.arrow([(897, 422), (885, 422)], "No", 878, 465)
    s.arrow([(1073, 422), (1092, 422)], "Yes", 1082, 398)
    s.arrow([(1180, 376), (1180, 336)], "No", 1205, 360)
    s.arrow([(1268, 422), (1300, 422)], "Yes", 1284, 398)
    s.arrow([(1375, 379), (1375, 336)], "Yes", 1400, 362)
    s.arrow([(1375, 465), (1375, 505)], "No", 1400, 489, C["green"][1])
    s.arrow([(1475, 308), (1505, 308), (1505, 547), (1483, 547)], "approved", 1478, 470, C["green"][1])
    s.arrow([(1278, 547), (1150, 547)], None, None, None, C["green"][1])
    s.arrow([(970, 547), (920, 547)], "Yes", 945, 538)
    s.arrow([(745, 547), (640, 547)], "restore", 692, 538)
    s.arrow([(1060, 589), (1060, 610), (525, 610), (525, 571)], "No", 800, 604, C["green"][1])
    s.arrow([(410, 548), (112, 548), (112, 75), (124, 75)], "loop", 112, 330, C["green"][1], dash=True)
    return s.render()


def context_chart():
    s = SVG(1590, 470)
    lanes = [(20, 205, "EVERY|ANSWER"), (230, 230, "EVERY|7 DAYS")]
    for i, (y, h, n) in enumerate(lanes):
        s.lane(y, h, n, i)
    s.term(125, 100, 150, 46, "💬 Question")
    s.diamond(360, 123, 150, 92, "Allowed?", ["IAM policy"], "red")
    s.box(310, 30, 100, 40, "403", [], "red")
    s.box(470, 90, 200, 66, "🔎 Local retrieval", ["ACL pre-filtered vectors + BM25", "ontology graph · project docs"], "teal")
    # context stack
    s.add(f'<rect x="700" y="34" width="296" height="180" rx="12" fill="#fbfcfd" stroke="#c9d4df" stroke-width="1.5" stroke-dasharray="6 5"/>')
    s.text(848, 54, "CONTEXT LAYERS · context_layers", 10.5, 800, C["mute"])
    for i, (t, sub, col) in enumerate([("📄 Role", "what this role needs", "blue"), ("🧑 Persona", "projects · scope · learned", "green"),
                                        ("🤖 Agent", "procedure · template · rules", "violet"), ("🧠 Memory", "preferences · recent turns", "amber")]):
        f, st = C[col]
        y = 64 + i * 37
        s.add(f'<rect x="713" y="{y}" width="270" height="31" rx="8" fill="{f}" stroke="{st}" stroke-width="{2.4 if t.endswith("Persona") else 1.3}"/>')
        s.text(725, y + 20, t, 12, 700, C["ink"], "start")
        s.text(972, y + 20, sub, 10.5, 500, C["mute"], "end")
    s.box(1030, 90, 150, 66, "🫥 Mask names", ["[PERSON_1] tokens", "outbound guard"], "red")
    s.diamond(1290, 123, 180, 92, "Private data?", ["board · HR · finance"], "red")
    s.box(1205, 30, 170, 40, "Local model only", [], "violet")
    s.box(1390, 150, 180, 60, "✅ Streamed answer", ["names restored · cited"], "green")
    s.box(1390, 270, 180, 62, "📡 What they do next", ["rephrase · correct · extend", "copy · ↻ · 👍👎"], "blue")
    s.box(1070, 270, 220, 62, "🔁 Person 7-day cycle", ["reviews every decision", "+ behaviour signals"], "amber")
    s.cyl(740, 260, 216, 84, "🍃 New persona version", ["'learned from my last cycle'", "screened · with reason"], "green")
    s.box(430, 275, 230, 56, "→ Agent improvement", ["same patterns, 2-person quorum"], "violet")
    # edges
    s.arrow([(275, 123), (285, 123)])
    s.arrow([(360, 77), (360, 70)], "No", 385, 82)
    s.arrow([(435, 123), (470, 123)], "Yes", 452, 115)
    s.arrow([(670, 123), (700, 123)])
    s.arrow([(996, 123), (1030, 123)])
    s.arrow([(1180, 123), (1200, 123)])
    s.arrow([(1290, 77), (1290, 70)], "Yes", 1318, 84)
    s.arrow([(1380, 123), (1480, 123), (1480, 150)], "No: any model", 1430, 113)
    s.arrow([(1480, 210), (1480, 270)])
    s.arrow([(1390, 301), (1290, 301)])
    s.arrow([(1070, 301), (956, 301)], None, None, None, C["green"][1])
    s.arrow([(848, 260), (848, 216)], "updates Persona", 922, 244, C["green"][1])
    s.arrow([(1180, 332), (1180, 400), (545, 400), (545, 331)], "evidence", 860, 394)
    return s.render()


if __name__ == "__main__":
    (OUT / "flow_agent.svg").write_text(agent_chart())
    (OUT / "flow_context.svg").write_text(context_chart())
    print("wrote", OUT / "flow_agent.svg", OUT / "flow_context.svg")
