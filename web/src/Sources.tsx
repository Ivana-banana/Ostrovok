// What a chat request asks for: always real data and the model. The backend answers by rules
// without a model key (TRAP-1), and the UI shows no switch for it.
export function requested(): { real_data: boolean; llm: boolean } {
  return { real_data: true, llm: true };
}
