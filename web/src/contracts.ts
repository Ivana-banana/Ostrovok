// Зеркало backend/src/api/contracts.py. Меняется в том же PR, что и бэкенд (метка contract).
// Даты и время — строки ISO 8601 (date: "2026-11-08", time: "14:00:00", с поясом: "...+03:00").
export type Stage = "clarify" | "inspire" | "select" | "compare" | "assemble" | "checkout" | "booked";
export type Meal = "RO" | "BB" | "HB" | "FB" | "AI";
export type Mode = "flight" | "train" | "bus" | "car";

export type TripSpec = {
  origin: string | null;
  destinations: string[];
  date_from: string | null;
  date_to: string | null;
  month: number | null;
  nights: number | null;
  adults: number;
  children_ages: number[];
  pets: boolean;
  budget_rub: number | null;
  meal: Meal | null;
  refundable: boolean | null;
  modes: Mode[];
  max_travel_hours: number | null;
  wishes: string[];
  assumptions: string[];
};

export type Cancellation = { refundable: boolean; free_until: string | null };

type Priced = { offer_id: string; title: string; price_rub: number; synthetic: boolean };

export type HotelOffer = Priced & {
  kind: "hotel";
  city: string;
  stars: number | null;
  rating: number | null;
  meal: Meal;
  cancellation: Cancellation;
  distance_center_km: number | null;
  lat: number;
  lon: number;
  check_in: string;
  check_out: string;
  pets_allowed: boolean;
  description: string;
};

export type TransportOffer = Priced & {
  kind: "transport";
  mode: Mode;
  origin: string;
  destination: string;
  depart_at: string;
  arrive_at: string;
  changes: number;
};

export type TransferOffer = Priced & { kind: "transfer"; depart_at: string; arrive_at: string };
export type ExtraOffer = Priced & { kind: "extra"; for_offer_id: string };
export type Offer = HotelOffer | TransportOffer | TransferOffer | ExtraOffer;

export type Destination = {
  name: string;
  country: string;
  lat: number;
  lon: number;
  why: string;
  est_total_rub: number;
  air_c: number | null;
  water_c: number | null;
  travel_hours: number;
  tradeoffs: string[];
};

export type Explanation = { offer_id: string; why: string; tradeoffs: string[]; risks: string[] };
export type TimelineEvent = { at: string; title: string; offer_id: string | null };
export type TimelineIssue = { severity: "warn" | "error"; text: string; fixes: string[] };

export type Plan = {
  spec: TripSpec;
  destinations: Destination[];
  offers: Offer[];
  selected: string[];
  explanations: Explanation[];
  timeline: TimelineEvent[];
  issues: TimelineIssue[];
  changes: string[];
};

export type Cart = { items: Offer[]; total_rub: number };

export type Voucher = {
  booking_id: string;
  items: Offer[];
  total_rub: number;
  created_at: string;
  simulated: true;
};

export type ChatRequest = { session_id: string | null; message: string };

export type ChatReply = {
  session_id: string;
  stage: Stage;
  reply: string;
  questions: string[];
  plan: Plan;
  cart: Cart;
  voucher: Voucher | null;
  llm: boolean;
};

export type CartAction = { session_id: string; action: "add" | "remove"; offer_id: string };
export type CheckoutRequest = { session_id: string; confirm_total_rub: number };
