import json
from pathlib import Path
import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, Select, Static
from textual.widgets.option_list import Option

API_BASE = "https://api.alquran.cloud/v1"
CONFIG_DIR = Path.home() / ".config" / "quran-tui"
STATE_FILE = CONFIG_DIR / "state.json"
CACHE_DIR = CONFIG_DIR / "cache"

FALLBACK_EDITIONS = [
    ("DE — Bubenheim & Elyas (de.bubenheim)", "de.bubenheim"),
    ("DE — Abu Rida (de.aburida)", "de.aburida"),
    ("DE — Khoury (de.khoury)", "de.khoury"),
    ("EN — Sahih International (en.sahih)", "en.sahih"),
    ("EN — Pickthall (en.pickthall)", "en.pickthall"),
    ("TR — Diyanet (tr.diyanet)", "tr.diyanet"),
]


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"surah": 1, "primary": "de.bubenheim", "secondary": "de.khoury", "compare": False, "bookmarks": []}


def save_state(state: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def read_cache(name: str):
    path = CACHE_DIR / f"{name}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def write_cache(name: str, data) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


class SettingsModal(ModalScreen[tuple[str, str] | None]):
    CSS = """
    SettingsModal { align: center middle; }
    #dialog { width: 68; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    Label { margin-top: 1; }
    #buttons { margin-top: 2; align: right middle; height: auto; }
    Button { margin-left: 1; }
    """

    def __init__(self, editions: list[tuple[str, str]], primary: str, secondary: str) -> None:
        super().__init__()
        self.editions = editions
        self.primary = primary
        self.secondary = secondary

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Translation Settings[/b] (Esc to cancel)")
            yield Label("Primary Translation:")
            yield Select(self.editions, value=self.primary, id="primary")
            yield Label("Comparison Translation (toggle with 'v'):")
            yield Select(self.editions, value=self.secondary, id="secondary")
            with Horizontal(id="buttons"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", variant="primary", id="save")

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            p = self.query_one("#primary", Select).value
            s = self.query_one("#secondary", Select).value
            if isinstance(p, str) and isinstance(s, str):
                self.dismiss((p, s))
                return
        self.dismiss(None)


class BookmarksModal(ModalScreen[int | None]):
    CSS = """
    BookmarksModal { align: center middle; }
    #dialog { width: 54; height: 22; border: thick $primary; background: $surface; padding: 1 2; }
    OptionList { height: 1fr; margin: 1 0; }
    #buttons { align: right middle; height: auto; }
    Button { margin-left: 1; }
    """

    def __init__(self, bookmarks: list[dict], current_surah: int, current_name: str) -> None:
        super().__init__()
        self.bookmarks = bookmarks
        self.current_surah = current_surah
        self.current_name = current_name

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Bookmarks[/b] (Enter to jump, 'd' to delete, Esc to close)")
            yield OptionList(
                *[Option(f"Surah {b['surah']}: {b['name']}", id=str(b["surah"])) for b in self.bookmarks],
                id="bm_list",
            )
            with Horizontal(id="buttons"):
                yield Button(f"+ Bookmark Surah {self.current_surah}", variant="success", id="add")
                yield Button("Close", id="close")

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self.dismiss(int(event.option.id))

    def on_key(self, event) -> None:
        if event.key == "d":
            ol = self.query_one("#bm_list", OptionList)
            if ol.highlighted is not None and 0 <= ol.highlighted < len(self.bookmarks):
                self.bookmarks.pop(ol.highlighted)
                ol.remove_option_at_index(ol.highlighted)
        elif event.key == "escape":
            self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            if not any(b["surah"] == self.current_surah for b in self.bookmarks):
                self.bookmarks.append({"surah": self.current_surah, "name": self.current_name})
                self.query_one("#bm_list", OptionList).add_option(
                    Option(f"Surah {self.current_surah}: {self.current_name}", id=str(self.current_surah))
                )
        else:
            self.dismiss(None)


class JumpModal(ModalScreen[tuple[int, int] | None]):
    CSS = """
    JumpModal { align: center middle; }
    #dialog { width: 46; height: auto; border: thick $primary; background: $surface; padding: 1 2; }
    Input { margin-top: 1; }
    """

    def __init__(self, current_surah: int) -> None:
        super().__init__()
        self.current_surah = current_surah

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("[b]Go to Verse[/b] (e.g. 2:255 or 18)")
            yield Input(placeholder=f"{self.current_surah}:1", id="jump_input")

    def on_mount(self) -> None:
        self.query_one("#jump_input", Input).focus()

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        raw = event.value.strip()
        if not raw:
            self.dismiss(None)
            return
        try:
            if ":" in raw:
                s_str, a_str = raw.split(":", 1)
                surah, ayah = int(s_str), int(a_str)
            else:
                surah, ayah = int(raw), 1
            if 1 <= surah <= 114 and ayah >= 1:
                self.dismiss((surah, ayah))
                return
        except ValueError:
            pass
        self.notify("Use format Surah:Verse (1-114), e.g. 2:255", severity="warning")


class QuranTUI(App):
    TITLE = "☪ ✦ K U R A N - I   K E R İ M ✦ ☪"
    CSS = """
    Header {
        color: #f1fa8c;
        text-style: bold;
    }
    #sidebar { width: 34; border-right: solid $primary; }
    #filter { margin: 0 1 1 1; }
    #surah_list { height: 1fr; }
    #reader { height: 1fr; padding: 1 2; }
    #reader:focus { border: tall $accent; }
    #surah_header { text-style: bold; color: $accent; margin-bottom: 1; }
    #verses { height: auto; }
    .ayah_single { height: auto; margin-bottom: 1; }
    .ayah_row { height: auto; margin-bottom: 1; }
    .ayah_col { width: 1fr; height: auto; padding-right: 2; }
    .ayah_highlight { background: $boost; border-left: thick $warning; padding-left: 1; }
    """
    BINDINGS = [
        ("g", "open_jump", "Go to"),
        ("v", "toggle_compare", "Compare"),
        ("c", "open_settings", "Translations"),
        ("b", "open_bookmarks", "Bookmarks"),
        ("/", "focus_search", "Search Surah"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.state = load_state()
        self.surahs: list[dict] = []
        self.editions: list[tuple[str, str]] = list(FALLBACK_EDITIONS)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Input(placeholder="Filter surah ('/' to focus)...", id="filter")
                yield OptionList(id="surah_list")
            reader = VerticalScroll(id="reader")
            reader.can_focus = True
            with reader:
                yield Static("Loading Surahs...", id="surah_header")
                yield Vertical(id="verses")
        yield Footer()

    def on_mount(self) -> None:
        self.theme = "dracula"
        self.fetch_metadata()

    @work(exclusive=True)
    async def fetch_metadata(self) -> None:
        cached_surahs = read_cache("meta_surahs")
        cached_editions = read_cache("meta_editions")
        if cached_surahs:
            self.surahs = cached_surahs
        if cached_editions:
            self.editions = [tuple(item) for item in cached_editions]

        if not self.surahs or not cached_editions:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    if not self.surahs:
                        r_surah = await client.get(f"{API_BASE}/surah")
                        r_surah.raise_for_status()
                        self.surahs = r_surah.json()["data"]
                        write_cache("meta_surahs", self.surahs)

                    if not cached_editions:
                        r_ed = await client.get(f"{API_BASE}/edition/type/translation")
                        if r_ed.status_code == 200:
                            raw = r_ed.json()["data"]
                            parsed = [
                                (f"{e['language'].upper()} — {e['englishName']} ({e['identifier']})", e["identifier"])
                                for e in raw
                                if e.get("format") == "text"
                            ]
                            if parsed:
                                self.editions = sorted(parsed, key=lambda x: x[0])
                                write_cache("meta_editions", self.editions)
            except Exception as exc:
                if not self.surahs:
                    self.query_one("#surah_header", Static).update(f"[red]Failed to load Surahs: {exc}[/red]")
                    return

        self.populate_surah_list(self.query_one("#filter", Input).value)
        self.load_surah(self.state["surah"])

    def populate_surah_list(self, query: str) -> None:
        ol = self.query_one("#surah_list", OptionList)
        ol.clear_options()
        q = query.strip().lower()
        active_num = self.state["surah"]
        active_idx = None
        for s in self.surahs:
            num = s["number"]
            plain = f"{num:>3}. {s['englishName']} ({s['numberOfAyahs']})"
            if not q or q in plain.lower() or q in s["englishNameTranslation"].lower():
                if num == active_num:
                    label = f"[bold #50fa7b]▶ {plain}[/bold #50fa7b]"
                    active_idx = ol.option_count
                else:
                    label = f"  {plain}"
                ol.add_option(Option(label, id=str(num)))
        if active_idx is not None:
            ol.highlighted = active_idx

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self.populate_surah_list(event.value)

    def on_key(self, event) -> None:
        search_input = self.query_one("#filter", Input)
        reader = self.query_one("#reader", VerticalScroll)
        if event.key == "escape" and search_input.has_focus:
            if search_input.value:
                search_input.value = ""
            reader.focus()
            event.stop()
        elif reader.has_focus:
            if event.key == "j":
                reader.scroll_down(animate=False)
            elif event.key == "k":
                reader.scroll_up(animate=False)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "surah_list" and event.option.id:
            self.load_surah(int(event.option.id))

    async def get_surah_edition(self, number: int, edition: str) -> dict:
        cache_key = f"surah_{number}_{edition}"
        cached = read_cache(cache_key)
        if cached:
            return cached
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(f"{API_BASE}/surah/{number}/{edition}")
            r.raise_for_status()
            data = r.json()["data"]
            write_cache(cache_key, data)
            return data

    @work(exclusive=True)
    async def load_surah(self, number: int, target_ayah: int = 1) -> None:
        self.state["surah"] = number
        save_state(self.state)
        self.populate_surah_list(self.query_one("#filter", Input).value)

        primary = self.state["primary"]
        secondary = self.state["secondary"]
        compare = self.state["compare"]

        header = self.query_one("#surah_header", Static)
        verses_box = self.query_one("#verses", Vertical)
        reader = self.query_one("#reader", VerticalScroll)
        header.update(f"Loading Surah {number}...")
        await verses_box.remove_children()

        try:
            data_1 = await self.get_surah_edition(number, primary)
            data_2 = await self.get_surah_edition(number, secondary) if compare else None
        except Exception as exc:
            header.update(f"[red]Error loading Surah {number}: {exc}[/red]")
            return

        mode_tag = f"  [{primary} | {secondary}]" if compare else f"  [{primary}]"
        header.update(
            f"{data_1['number']}. {data_1['englishName']} — {data_1['englishNameTranslation']} "
            f"({data_1['revelationType']}, {data_1['numberOfAyahs']} Ayahs){mode_tag}"
        )

        ayahs_1 = data_1["ayahs"]
        ayahs_2 = data_2["ayahs"] if data_2 else None
        target_widget = None

        widgets = []
        for i, a1 in enumerate(ayahs_1):
            num = a1["numberInSurah"]
            is_target = num == target_ayah and target_ayah > 1
            if ayahs_2:
                row_classes = "ayah_row ayah_highlight" if is_target else "ayah_row"
                w = Horizontal(
                    Static(f"[b][yellow]{num}.[/yellow][/b] {a1['text']}", classes="ayah_col"),
                    Static(f"[b][yellow]{num}.[/yellow][/b] {ayahs_2[i]['text']}", classes="ayah_col"),
                    classes=row_classes,
                )
            else:
                single_classes = "ayah_single ayah_highlight" if is_target else "ayah_single"
                w = Static(f"[b][yellow]{num}.[/yellow][/b] {a1['text']}", classes=single_classes)
            if num == target_ayah:
                target_widget = w
            widgets.append(w)

        await verses_box.mount(*widgets)
        reader.focus()
        if target_widget and target_ayah > 1:
            self.call_after_refresh(lambda: reader.scroll_to_widget(target_widget, top=True, animate=False))
        else:
            reader.scroll_home(animate=False)

    def action_open_jump(self) -> None:
        def on_close(result: tuple[int, int] | None) -> None:
            if result:
                surah, ayah = result
                self.load_surah(surah, target_ayah=ayah)

        self.push_screen(JumpModal(self.state["surah"]), on_close)

    def action_focus_search(self) -> None:
        self.query_one("#filter", Input).focus()

    def action_toggle_compare(self) -> None:
        self.state["compare"] = not self.state["compare"]
        save_state(self.state)
        self.load_surah(self.state["surah"])

    def action_open_settings(self) -> None:
        def on_close(result: tuple[str, str] | None) -> None:
            if result:
                self.state["primary"], self.state["secondary"] = result
                save_state(self.state)
                self.load_surah(self.state["surah"])

        self.push_screen(SettingsModal(self.editions, self.state["primary"], self.state["secondary"]), on_close)

    def action_open_bookmarks(self) -> None:
        current = next((s for s in self.surahs if s["number"] == self.state["surah"]), None)
        name = current["englishName"] if current else ""

        def on_close(surah_num: int | None) -> None:
            save_state(self.state)
            if surah_num:
                self.load_surah(surah_num)

        self.push_screen(BookmarksModal(self.state["bookmarks"], self.state["surah"], name), on_close)


def main() -> None:
    QuranTUI().run()


if __name__ == "__main__":
    main()

