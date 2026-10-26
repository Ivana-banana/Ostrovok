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
  meal: Meal | null;
  refundable: boolean | null;
  modes: Mode[];
  max_travel_hours: number | null;
  wishes: string[];
  priorities: Priority[];
  assumptions: string[];
};

export type Cancellation = { refundable: boolean; free_until: string | null };

type Priced = { offer_id: string; title: string; price_rub: number; synthetic: boolean };

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

export type Voucher = {
  booking_id: string;
  items: Offer[];
  spec: TripSpec;
  total_rub: number;
  created_at: string;
  simulated: true;
};

export type ChatRequest = { session_id: string | null; message: string };

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

export type CartAction = { session_id: string; action: "add" | "remove"; offer_id: string };
export type CheckoutRequest = { session_id: string; confirm_total_rub: number };
