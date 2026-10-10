/**
 * 화면의 글을 운영자가 고쳐 쓴 것으로 바꿔 보여 주고, 주소 끝에 ?edit 를 붙여 열면 화면에서 바로 고칠 수 있게 한다.
 *
 * 글마다 이름표를 붙이지 않는다. 화면에 그려진 글 조각(텍스트 노드) 하나하나를 "코드에 적힌 원래 글"로 알아보고,
 * 서버에 {원래 글: 고친 글}이 있으면 고친 글로 바꿔 둔다. 그래서 어느 페이지의 어느 글이든 코드를 고치지 않고 바꿀 수 있다.
 * 숫자가 섞여 날마다 달라지는 글(예: "기업 14,514곳")은 원래 글이 달라지면 고친 것이 풀린다.
 */

type Texts = Record<string, string>;

const squeeze = (text: string) => text.replace(/\s+/g, " ").trim();
const SKIP = new Set(["SCRIPT", "STYLE", "TEXTAREA", "INPUT", "SELECT", "OPTION", "CANVAS", "CODE"]);

let texts: Texts = {};
let owner = false;
/** 글 조각의 원래 글과, 우리가 바꿔 넣은 글. 화면 쪽(React)이 글을 새로 쓰면 바꿔 넣은 글과 달라지므로 알아챌 수 있다 */
const originals = new WeakMap<Text, string>();
const applied = new WeakMap<Text, string>();
let observer: MutationObserver | null = null;
let queued = false;

function ours(node: Node): boolean {
  for (let el = node.parentElement; el; el = el.parentElement) {
    if (el.id === "text-editor" || SKIP.has(el.tagName)) return true;
  }
  return false;
}

/** 글 조각 하나에 고친 글을 입힌다. 앞뒤의 빈칸은 그대로 둔다 */
function dress(node: Text) {
  const now = node.nodeValue ?? "";
  if (originals.has(node) && applied.get(node) !== now) originals.delete(node);   // 화면이 새 글을 썼다. 그것이 새 원래 글이다
  const original = originals.get(node) ?? now;
  const key = squeeze(original);
  if (!key) return;
  const edited = texts[key];
  if (edited === undefined) {
    if (originals.has(node)) {
      node.nodeValue = original;
      originals.delete(node);
      applied.delete(node);
    }
    return;
  }
  const next = original.replace(/\S[\s\S]*\S|\S/, () => edited);
  originals.set(node, original);
  applied.set(node, next);
  if (now !== next) node.nodeValue = next;
}

function sweep() {
  queued = false;
  observer?.disconnect();
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (!ours(node)) dress(node as Text);
  }
  observer?.observe(document.body, { childList: true, subtree: true, characterData: true });
}

function schedule() {
  if (queued) return;
  queued = true;
  setTimeout(sweep, 0);
}

async function save(original: string, text: string | null): Promise<string | null> {
  let key: string | null = null;
  try {
    key = localStorage.getItem("owner-key");
  } catch {
    /* 열쇠 없이 보낸다. 내 컴퓨터에서 띄운 서버는 받아 준다 */
  }
  const response = await fetch("/api/texts", {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...(key ? { "X-Owner-Key": key } : {}) },
    body: JSON.stringify({ original, text }),
  });
  if (response.status === 403) return "운영자만 저장할 수 있습니다. 주소 끝에 #owner=열쇠 를 붙여 한 번 열어 열쇠를 등록하세요.";
  if (!response.ok) return "저장하지 못했습니다.";
  if (text === null || text === original) delete texts[original];
  else texts[original] = text;
  schedule();
  return null;
}

// ---------- 고치는 화면 (?edit) ----------

function el<K extends keyof HTMLElementTagNameMap>(tag: K, style: string, text = ""): HTMLElementTagNameMap[K] {
  const made = document.createElement(tag);
  made.style.cssText = style;
  made.textContent = text;
  return made;
}

const BUTTON = "padding:6px 12px;border:0;border-radius:3px;font:inherit;font-size:13px;cursor:pointer;";

function editor() {
  let editing = true;   // 켜져 있으면 글을 누를 때 고치는 칸이 뜬다. 끄면 평소처럼 누를 수 있다
  const root = el("div", "position:fixed;left:0;right:0;bottom:0;z-index:9999;font-family:inherit;font-size:13px;");
  root.id = "text-editor";
  const bar = el("div", "display:flex;align-items:center;gap:12px;padding:8px 16px;background:#1b1c1f;color:#f1f1f1;border-top:2px solid #e2b04a;");
  const label = el("b", "", "글 고치기");
  const mode = el("button", BUTTON + "background:#e2b04a;color:#14110a;font-weight:600;", "글 고치는 중 (누르면 화면 이동으로)");
  const count = el("span", "color:#b9c0cb;");
  const list = el("button", BUTTON + "background:#33353b;color:#f1f1f1;", "고친 글 보기");
  const hint = el("span", "margin-left:auto;color:#b9c0cb;", owner ? "글을 누르면 고칠 수 있습니다" : "보기만 됩니다 (운영자 열쇠 없음)");
  bar.append(label, mode, count, list, hint);
  const panel = el("div", "display:none;max-height:40vh;overflow:auto;padding:12px 16px;background:#24252a;color:#f1f1f1;");
  const box = el("div", "display:none;position:fixed;z-index:10000;width:min(560px,90vw);padding:12px;background:#24252a;color:#f1f1f1;border:1px solid #55585f;border-radius:4px;");
  box.id = "text-editor";
  root.append(panel, bar);
  document.body.append(root, box);

  const refresh = () => {
    count.textContent = `고친 글 ${Object.keys(texts).length}건`;
    mode.textContent = editing ? "글 고치는 중 (누르면 화면 이동으로)" : "화면 이동 중 (누르면 글 고치기로)";
    mode.style.background = editing ? "#e2b04a" : "#33353b";
    mode.style.color = editing ? "#14110a" : "#f1f1f1";
    document.documentElement.style.cursor = editing ? "text" : "";
    if (panel.style.display !== "none") showList();
  };
  mode.onclick = () => {
    editing = !editing;
    box.style.display = "none";
    refresh();
  };

  function showList() {
    panel.replaceChildren();
    const keys = Object.keys(texts);
    if (!keys.length) panel.append(el("p", "margin:0;color:#b9c0cb;", "아직 고친 글이 없습니다."));
    for (const original of keys) {
      const row = el("div", "display:grid;grid-template-columns:1fr 1fr auto;gap:12px;align-items:start;padding:8px 0;border-bottom:1px solid #3a3c42;");
      const undo = el("button", BUTTON + "background:#33353b;color:#f1f1f1;", "원래대로");
      undo.onclick = async () => {
        const failed = await save(original, null);
        if (failed) alert(failed);
        refresh();
      };
      row.append(el("div", "color:#9a9ca3;white-space:pre-wrap;", original), el("div", "white-space:pre-wrap;", texts[original]), undo);
      panel.append(row);
    }
  }
  list.onclick = () => {
    panel.style.display = panel.style.display === "none" ? "block" : "none";
    if (panel.style.display !== "none") showList();
  };

  function open(node: Text, x: number, y: number) {
    const original = squeeze(originals.get(node) ?? node.nodeValue ?? "");
    if (!original) return;
    box.replaceChildren();
    const area = el("textarea", "width:100%;min-height:90px;padding:8px;background:#17181b;color:#f1f1f1;border:1px solid #55585f;border-radius:3px;font:inherit;font-size:14px;line-height:1.5;resize:vertical;");
    area.value = texts[original] ?? original;
    const was = el("p", "margin:8px 0 0;color:#9a9ca3;font-size:12px;white-space:pre-wrap;", texts[original] !== undefined ? `원래 글: ${original}` : "");
    const note = el("p", "margin:8px 0 0;color:#f0b354;font-size:12px;");
    const buttons = el("div", "display:flex;gap:8px;margin-top:10px;");
    const ok = el("button", BUTTON + "background:#e2b04a;color:#14110a;font-weight:600;", "저장");
    const undo = el("button", BUTTON + "background:#33353b;color:#f1f1f1;", "원래대로");
    const cancel = el("button", BUTTON + "background:none;color:#b9c0cb;", "닫기");
    const done = async (text: string | null) => {
      const failed = await save(original, text);
      if (failed) {
        note.textContent = failed;
        return;
      }
      box.style.display = "none";
      refresh();
    };
    ok.onclick = () => done(area.value.trim() || null);
    undo.onclick = () => done(null);
    cancel.onclick = () => (box.style.display = "none");
    area.onkeydown = (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) ok.click();
      if (event.key === "Escape") cancel.click();
    };
    buttons.append(ok, undo, cancel, el("span", "margin-left:auto;align-self:center;color:#9a9ca3;font-size:12px;", "Ctrl+Enter 저장"));
    box.append(area, was, note, buttons);
    box.style.display = "block";
    box.style.left = `${Math.max(8, Math.min(x, window.innerWidth - 580))}px`;
    box.style.top = `${Math.max(8, Math.min(y + 14, window.innerHeight - 260))}px`;
    area.focus();
    area.select();
  }

  // 글을 누르면 평소의 동작(탭 이동, 줄 펴기) 대신 고치는 칸을 띄운다
  document.addEventListener(
    "click",
    (event) => {
      if (!editing || !owner) return;
      const target = event.target as HTMLElement;
      if (target.closest("#text-editor")) return;
      const at = document.caretRangeFromPoint?.(event.clientX, event.clientY)?.startContainer;
      const node = at?.nodeType === Node.TEXT_NODE ? (at as Text) : null;
      if (!node || ours(node) || !squeeze(node.nodeValue ?? "")) return;
      event.preventDefault();
      event.stopPropagation();
      open(node, event.clientX, event.clientY);
    },
    true,
  );
  refresh();
}

/** 앱이 뜰 때 한 번 부른다. 고쳐 쓴 글을 받아 입히고, ?edit 로 열었으면 고치는 띠를 단다 */
export async function startTexts() {
  try {
    const response = await fetch("/api/texts", {
      headers: (() => {
        try {
          const key = localStorage.getItem("owner-key");
          return key ? { "X-Owner-Key": key } : undefined;
        } catch {
          return undefined;
        }
      })(),
    });
    if (response.ok) ({ texts, owner } = (await response.json()) as { texts: Texts; owner: boolean });
  } catch {
    /* 받지 못하면 코드에 적힌 글 그대로 보인다 */
  }
  const editing = new URLSearchParams(window.location.search).has("edit");
  if (!editing && Object.keys(texts).length === 0) return;   // 고친 글이 없으면 화면을 지켜볼 일도 없다
  observer = new MutationObserver(schedule);
  sweep();
  if (editing) editor();
}
