import type { SummaryRange } from "@/api/types";
import type { Delta } from "@/components/StatTile";
import { formatDeltaPoints, formatDeltaRelative } from "@/design/format";

type Maybe = number | null | undefined;

const NO_DATA: Delta = { text: "—", direction: null, tone: "neutral" };

export const VERSUS: Record<SummaryRange, string> = {
  "24h": "vs 24 h ant.",
  "7d": "vs 7 d ant.",
  "30d": "vs 30 d ant.",
};

function build(
  value: Maybe,
  previous: Maybe,
  text: string,
  versus: string,
  higherIsBetter: boolean,
): Delta {
  if (value == null || previous == null || text === "—") return NO_DATA;
  const diff = value - previous;
  if (diff === 0) return { text, direction: null, tone: "neutral", versus };
  const up = diff > 0;
  return {
    text,
    direction: up ? "up" : "down",
    tone: up === higherIsBetter ? "good" : "bad",
    versus,
  };
}

/** Variación relativa de una cantidad (más eventos o más tiempo con EPP = bueno). Sin periodo
 * previo (o previo 0) muestra "—", nunca "+∞ %". */
export function relativeDelta(value: Maybe, previous: Maybe, range: SummaryRange): Delta {
  return build(value, previous, formatDeltaRelative(value, previous), VERSUS[range], true);
}

/** Variación en puntos de una fracción 0-1 (menos confianza o menos cobertura = malo). */
export function pointsDelta(value: Maybe, previous: Maybe, range: SummaryRange): Delta {
  return build(value, previous, formatDeltaPoints(value, previous), VERSUS[range], true);
}
