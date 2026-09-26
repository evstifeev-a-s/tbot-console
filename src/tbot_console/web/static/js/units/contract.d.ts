export type Tone = "ok" | "warn" | "bad" | "idle";
export type Cleanup = () => void;
export type Query = Record<string, string | number | boolean | null | undefined>;
export type Json = ReturnType<typeof JSON.parse>;

export interface ServiceError extends Error {
  status?: number;
  detail?: unknown;
}

export interface Client {
  get(path: string, query?: Query, signal?: AbortSignal): Promise<Json>;
  send(
    method: "POST" | "PUT" | "PATCH" | "DELETE",
    path: string,
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<Json>;
}

export interface Metric {
  label: string;
  value: string;
  hint?: string;
  tone?: Tone;
}

export interface Problem {
  text: string;
  tone: Tone;
}

export interface UnitStatus {
  v: 1;
  tone: Tone;
  headline: string;
  metrics: Metric[];
  problems: Problem[];
  updated_at: number;
}

export type ProcessState =
  | "running"
  | "starting"
  | "stopping"
  | "checking"
  | "stopped"
  | "exited"
  | "stuck"
  | "interrupted"
  | "foreign";

export type ProcessAction = "start" | "stop" | "restart" | "adopt";

export interface ProcessOp {
  op_id: string;
  action: ProcessAction;
  phase: string;
  error: string | null;
}

export interface ProcessView {
  name: string;
  title: string;
  state: ProcessState;
  label: string;
  pid: number | null;
  since: number | null;
  adopted: boolean;
  can: ProcessAction[];
  confirm: string | null;
  op: ProcessOp | null;
  problem: string | null;
  output: boolean;
}

export interface UnitApi {
  url: string;
  reachable: boolean | null;
  hint: string | null;
}

export interface UnitEntry {
  id: string;
  kind: "strategy" | "monitor" | "system";
  title: string;
  glyph: string;
  sub: string;
  order: number;
  ui: string | null;
  places_orders: boolean;
  legacy_pages: string[];
  api: UnitApi | null;
  processes: ProcessView[];
  status: UnitStatus | null;
  status_error: string | null;
}

export interface Registry {
  v: 1;
  control: boolean;
  units: UnitEntry[];
  broken: { file: string; error: string }[];
}

export interface UnitStorage {
  get(key: string): string | null;
  set(key: string, value: string | null): void;
}

export interface UnitSdk {
  readonly id: string;
  readonly api: Client;
  href(page: string, arg?: string): string;
  navigate(page: string, arg?: string): void;
  rerender(): void;
  readonly storage: UnitStorage;
}

export interface UnitPage {
  id: string;
  glyph: string;
  label: string;
}

export interface UnitHost {
  readonly topbar: HTMLElement;
  banner(text: string | null): void;
}

export interface UnitRoute {
  readonly page: string;
  readonly arg: string;
  readonly signal: AbortSignal;
}

export interface UnitUi {
  readonly pages: readonly UnitPage[];
  activate(host: UnitHost): Cleanup | undefined;
  render(view: HTMLElement, route: UnitRoute): Cleanup | undefined;
}

export type CreateUnitUi = (sdk: UnitSdk) => UnitUi;
