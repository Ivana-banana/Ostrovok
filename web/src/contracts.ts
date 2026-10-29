// Mirror of backend/src/api/contracts.py. Changes in the same PR as the backend (label contract).
// Dates and times are ISO 8601 strings (date "2026-11-08", time "14:00:00", zoned "...+03:00").
export type Stage = "clarify" | "inspire" | "select" | "compare" | "assemble" | "checkout" | "booked";
export type Meal = "RO" | "BB" | "HB" | "FB" | "AI";
export type Mode = "flight" | "train" | "car";
export type Priority = "price" | "cancellation" | "meal" | "location" | "travel_time" | "rating";
export type Amenity = "family" | "kids_club" | "wheelchair" | "elevator" | "beach" | "parking";

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
  hotel_budget_rub: number | null;
  meal: Meal | null;
  refundable: boolean | null;
  modes: Mode[];
  max_travel_hours: number | null;
  own_transport: boolean; // C5 (#408): own tickets, no transport searched
  arrive_at: string | null; // own arrival, zoned
  leave_at: string | null; // own departure, zoned
  wishes: string[];
  priorities: Priority[];
  assumptions: string[];
};

export type Cancellation = { refundable: boolean; free_until: string | null };

// #469: source is the provider of the price ("101hotels", "travelpayouts", "tutu", "синтетика"),
// fetched_at when it answered (null for a synthetic offer).
type Priced = {
  offer_id: string;
  title: string;
  price_rub: number;
  synthetic: boolean;
  source: string;
  fetched_at: string | null;
};

export type HotelOffer = Priced & {
  kind: "hotel";
  city: string;
  tz: string;
  date_in: string;
  date_out: string;
  adults: number;
  children_ages: number[];
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
  amenities: Amenity[];
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
  pets_allowed: boolean;
};

// Pair of the ride and the provider id (ADR-0002, #307); the web does not read them yet.
export type TransferOffer = Priced & {
  kind: "transfer";
  depart_at: string;
  arrive_at: string;
  hotel_offer_id?: string | null;
  leg_offer_id?: string | null;
  ref?: string | null;
};
// tag: "early" | "late" | "prev" or another add-on; at: local time "HH:MM:SS" (#307).
export type ExtraOffer = Priced & {
  kind: "extra";
  for_offer_id: string;
  tag?: string | null;
  at?: string | null;
};
export type Offer = HotelOffer | TransportOffer | TransferOffer | ExtraOffer;

export type Destination = {
  name: string;
  country: string;
  lat: number;
  lon: number;
  why: string;
  est_total_rub: number;
  synthetic: boolean;
  air_c: number | null;
  water_c: number | null;
  travel_hours: number;
  tradeoffs: string[];
  violates: string[];
  risks: string[];
};

export type Explanation = { offer_id: string; why: string; tradeoffs: string[]; risks: string[] };
export type TimelineEvent = { at: string; title: string; offer_id: string | null };
export type TimelineIssue = { severity: "warn" | "error"; text: string; fixes: string[] };

export type Change = { what: string; before: string; after: string };
export type Question = { text: string; options: string[] };

export type Bundle = {
  bundle_id: string;
  label: string;
  offer_ids: string[];
  total_rub: number;
  travel_hours: number;
  tradeoffs: string[];
};

export type Plan = {
  spec: TripSpec;
  destinations: Destination[];
  offers: Offer[];
  bundles: Bundle[];
  selected: string[];
  explanations: Explanation[];
  timeline: TimelineEvent[];
  issues: TimelineIssue[];
  changes: Change[];
};

export type Cart = { items: Offer[]; total_rub: number };

// From the BookingProvider; a refusal is a 409, not a status (ADR-0002).
export type BookingStatus = "created" | "confirmed" | "cancelled";

export type Voucher = {
  booking_id: string;
  items: Offer[];
  spec: TripSpec;
  total_rub: number;
  created_at: string;
  status: BookingStatus;
  simulated: boolean; // true: booking and payment are simulated; the voucher says so
  guest: Guest | null; // null: no name given
};

// Lead guest typed at checkout (#367); simulated, stored with the session, not sent to the provider.
export type Guest = { name: string; contact: string };

// #566: real_data — the live sources (false: all synthetic), llm — the model; both true by default.
export type ChatRequest = {
  session_id: string | null;
  message: string;
  real_data?: boolean;
  llm?: boolean;
};

export type ChatReply = {
  session_id: string;
  stage: Stage;
  reply: string;
  questions: Question[];
  plan: Plan;
  cart: Cart;
  voucher: Voucher | null;
  llm: boolean;
};

// GET /api/mode: what the switches can turn on (#490). llm false: no model in .env, rules only
// (TRAP-1); real_data false: no live source is set up, every price is synthetic.
export type LlmMode = { llm: boolean; real_data: boolean };

export type CartAction = { session_id: string; action: "add" | "remove"; offer_id: string };
export type CheckoutRequest = {
  session_id: string;
  confirm_total_rub: number;
  guest?: Guest | null;
};
