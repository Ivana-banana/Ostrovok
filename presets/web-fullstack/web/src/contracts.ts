// Зеркало backend/src/api/contracts.py. Меняется в том же PR, что и бэкенд (метка contract).
export type PredictRequest = { text: string };
export type Prediction = { label: string; score: number; explanation: string; stub: boolean };
