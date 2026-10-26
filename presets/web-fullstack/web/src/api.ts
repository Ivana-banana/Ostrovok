import type { Prediction, PredictRequest } from "./contracts";

export async function predict(req: PredictRequest): Promise<Prediction> {
  const r = await fetch("/api/predict", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`API ${r.status}`);
  return (await r.json()) as Prediction;
}
