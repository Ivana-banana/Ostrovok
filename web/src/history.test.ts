import { afterEach, beforeEach, describe as suite, expect, it, vi } from "vitest";
import {
  HISTORY_KEY,
  chatTitle,
  filterChats,
  groupChats,
  loadChats,
  removeChat,
  renameChat,
  saveChats,
  touchChat,
} from "./history";

beforeEach(() => localStorage.clear());
afterEach(() => vi.restoreAllMocks());

const boom = () => {
  throw new DOMException("blocked", "SecurityError");
};

suite("список чатов", () => {
  it("новый чат получает название по первому сообщению, свежий — наверху", () => {
    let list = touchChat([], "a", "  Казань\n на выходные  ", 1);
    list = touchChat(list, "b", "Сочи с детьми", 2);
    expect(list.map((c) => c.session_id)).toEqual(["b", "a"]);
    expect(list[1].title).toBe("Казань на выходные");
    list = touchChat(list, "a", "другой текст", 3);
    expect(list.map((c) => c.session_id)).toEqual(["a", "b"]);
    expect(list[0].title).toBe("Казань на выходные"); // the old title stays
  });

  it("длинное название обрезается, пустое — «Новая поездка»", () => {
    const t = chatTitle("о".repeat(200));
    expect(t.length).toBe(60);
    expect(t.endsWith("…")).toBe(true);
    expect(chatTitle("   ")).toBe("Новая поездка");
  });

  it("переименование и удаление меняют только свой чат", () => {
    const list = [touchChat([], "a", "A", 1)[0], touchChat([], "b", "B", 2)[0]];
    expect(renameChat(list, "a", " Отпуск ").map((c) => c.title)).toEqual(["Отпуск", "B"]);
    expect(removeChat(list, "a").map((c) => c.session_id)).toEqual(["b"]);
  });

  it("сохраняется и читается из хранилища; чужие данные отброшены", () => {
    saveChats(touchChat([], "a", "A", 1));
    expect(loadChats()).toEqual([{ session_id: "a", title: "A", updated_at: 1 }]);
    localStorage.setItem(HISTORY_KEY, JSON.stringify([{ session_id: "x" }, null, 5, { session_id: "y", title: "Y", updated_at: 2 }]));
    expect(loadChats().map((c) => c.session_id)).toEqual(["y"]);
    localStorage.setItem(HISTORY_KEY, "{broken");
    expect(loadChats()).toEqual([]);
  });

  it("хранилище бросает ошибку — пустой список, запись не падает", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
    expect(loadChats()).toEqual([]);
    expect(() => saveChats(touchChat([], "a", "A"))).not.toThrow();
  });

  it("поиск по названию без учёта регистра; пустой запрос — весь список", () => {
    const list = [touchChat([], "a", "Казань на выходные", 1)[0], touchChat([], "b", "Сочи с детьми", 2)[0]];
    expect(filterChats(list, "  казань ").map((c) => c.session_id)).toEqual(["a"]);
    expect(filterChats(list, "")).toEqual(list);
    expect(filterChats(list, "Байкал")).toEqual([]);
  });

  it("группы «Сегодня», «Вчера», «Ранее» по дню updated_at; пустые группы не показаны", () => {
    const now = new Date(2026, 9, 27, 15, 0).getTime();
    const at = (d: number, h: number) => new Date(2026, 9, d, h, 0).getTime();
    const list = [
      { session_id: "t", title: "T", updated_at: at(27, 0) },
      { session_id: "y", title: "Y", updated_at: at(26, 23) },
      { session_id: "y0", title: "Y0", updated_at: at(26, 0) },
      { session_id: "o", title: "O", updated_at: at(25, 23) },
    ];
    expect(groupChats(list, now).map((g) => [g.label, g.items.map((c) => c.session_id)])).toEqual([
      ["Сегодня", ["t"]],
      ["Вчера", ["y", "y0"]],
      ["Ранее", ["o"]],
    ]);
    expect(groupChats([list[3]], now).map((g) => g.label)).toEqual(["Ранее"]);
  });
});
