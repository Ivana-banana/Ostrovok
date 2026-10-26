import { useState } from "react";
import { predict } from "./api";
import { describe } from "./format";

export function App() {
  const [text, setText] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(describe(await predict({ text })));
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 720, margin: "2rem auto" }}>
      <h1>Демо</h1>
      <form onSubmit={submit}>
        <label>
          Запрос <input aria-label="Запрос" value={text} onChange={(e) => setText(e.target.value)} />
        </label>
        <button type="submit" disabled={busy}>
          {busy ? "Считаю…" : "Отправить"}
        </button>
      </form>
      {result && <p role="status">{result}</p>}
      {error && <p role="alert">{error}</p>}
    </main>
  );
}
