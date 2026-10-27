import { afterEach, beforeEach, describe as suite, expect, it, vi } from "vitest";
import { HISTORY_KEY, chatTitle, loadChats, removeChat, renameChat, saveChats, touchChat } from "./history";
import { THEME_KEY, initialTheme } from "./theme";

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
});

suite("тема", () => {
  it("по умолчанию «banana»; ?theme=lime выбирает и запоминает лайм", () => {
    expect(initialTheme("")).toBe("banana");
    expect(initialTheme("?theme=lime")).toBe("lime");
    expect(localStorage.getItem(THEME_KEY)).toBe("lime");
    expect(initialTheme("")).toBe("lime");
    expect(initialTheme("?theme=pink")).toBe("lime");
  });

  it("хранилище бросает ошибку — тема из адреса или по умолчанию", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(boom);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(boom);
    expect(initialTheme("?theme=lime")).toBe("lime");
    expect(initialTheme("")).toBe("banana");
  });
});
