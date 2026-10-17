import type { components, operations } from "./schema";

type S = components["schemas"];

export type Me = S["Me"];
export type Role = Me["role"];
export type DeviceStatus = S["DeviceStatus"];
export type DeviceState = DeviceStatus["state"];
export type Summary = S["Summary"];
export type SummaryRange = Summary["range"];
export type EventSummary = S["EventSummary"];
export type EventDetail = S["EventDetail"];
export type EventPage = S["EventPage"];
export type CaptureStatus = EventSummary["capture_status"];
export type DetectionBox = S["DetectionBox"];
export type UserOut = S["UserOut"];
export type AuditEntry = S["AuditEntry"];
export type AuditPage = S["AuditPage"];

export type EventsQuery = NonNullable<
  operations["device_events_api_devices__device_id__events_get"]["parameters"]["query"]
>;

export interface ApiErrorBody {
  error: { code: string; message: string };
}
