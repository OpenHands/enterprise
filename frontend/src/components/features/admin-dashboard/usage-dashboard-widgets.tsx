/* eslint-disable i18next/no-literal-string */
import React from "react";
import {
  TrendDownIcon,
  TrendUpIcon,
} from "#/components/shared/icons/inline-icons";
import { formatCost, formatShortDate } from "./usage-dashboard-utils";

type ChartHoverPoint = {
  index: number;
  xPct: number;
  clientX: number;
  clientY: number;
};

function nearestPointIndex(
  event: React.MouseEvent<HTMLElement>,
  pointCount: number,
) {
  const rect = event.currentTarget.getBoundingClientRect();
  if (rect.width <= 0 || pointCount < 1) {
    return 0;
  }
  const ratio = Math.min(
    1,
    Math.max(0, (event.clientX - rect.left) / rect.width),
  );
  return Math.round(ratio * (pointCount - 1));
}

function ChartTooltip({
  x,
  y,
  children,
}: {
  x: number;
  y: number;
  children: React.ReactNode;
}) {
  return (
    <div
      data-testid="usage-chart-tooltip"
      className="pointer-events-none absolute z-10 min-w-28 -translate-x-1/2 -translate-y-[calc(100%+10px)] rounded-lg border border-border-subtle bg-base-secondary px-3 py-2 shadow-lg"
      style={{ left: x, top: y }}
    >
      {children}
    </div>
  );
}

/**
 * HTML overlay dot — SVG circles flatten under `preserveAspectRatio="none"`.
 */
function ChartHoverDot({
  xPct,
  yPct,
  color,
}: {
  xPct: number;
  yPct: number;
  color: string;
}) {
  return (
    <span
      aria-hidden
      data-testid="usage-chart-hover-dot"
      className="pointer-events-none absolute z-[1] size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[var(--oh-color-base)]"
      style={{
        left: `${xPct}%`,
        top: `${yPct}%`,
        backgroundColor: color,
      }}
    />
  );
}

export function KPICard({
  label,
  value,
  trend,
  trendUp,
}: {
  label: string;
  value: string | number;
  trend?: string;
  trendUp?: boolean;
}) {
  return (
    <div className="flex h-full min-h-[104px] flex-col rounded-lg border border-border-subtle bg-base-secondary px-4 py-5">
      <span className="text-xs font-medium leading-tight text-muted">
        {label}
      </span>
      <div className="mt-auto pt-3 text-left text-2xl font-bold leading-none text-foreground">
        {value}
      </div>
      {trend && (
        <div
          className={`mt-2 flex items-center gap-1 text-xs ${trendUp ? "text-green-400" : "text-red-400"}`}
        >
          {trendUp ? <TrendUpIcon /> : <TrendDownIcon />}
          {trend}
        </div>
      )}
    </div>
  );
}

export type ChartSeries = {
  id: string;
  label: string;
  color: string;
  values: number[];
};

export function MultiLineChart({
  dates,
  series,
}: {
  dates: string[];
  series: ChartSeries[];
}) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [hover, setHover] = React.useState<ChartHoverPoint | null>(null);
  const pointCount = series[0]?.values.length ?? 0;
  const maxValue = Math.max(1, ...series.flatMap((row) => row.values));
  const width = 100;
  const height = 100;
  const dateStep = Math.max(1, Math.ceil(dates.length / 7));

  if (pointCount < 2) {
    return (
      <div className="flex h-full min-h-36 items-center justify-center text-sm text-muted">
        No usage data available yet.
      </div>
    );
  }

  const updateHover = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    const index = nearestPointIndex(event, pointCount);
    setHover({
      index,
      xPct: (index / (pointCount - 1)) * 100,
      clientX: event.clientX - rect.left,
      clientY: event.clientY - rect.top,
    });
  };

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full pb-5"
      onMouseMove={updateHover}
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative h-full w-full">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-full w-full"
          preserveAspectRatio="none"
        >
          {[0, 25, 50, 75, 100].map((pct) => (
            <line
              key={pct}
              x1="0"
              y1={`${pct}%`}
              x2="100%"
              y2={`${pct}%`}
              stroke="var(--oh-border-subtle)"
              strokeWidth="0.5"
            />
          ))}
          {series.map((row) => {
            const points = row.values.map((value, index) => {
              const x = (index / (pointCount - 1)) * width;
              const y = height - (value / maxValue) * height;
              return `${x},${y}`;
            });
            return (
              <path
                key={row.id}
                d={`M ${points.join(" L ")}`}
                fill="none"
                stroke={row.color}
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
              />
            );
          })}
          {hover && (
            <line
              x1={hover.xPct}
              y1="0"
              x2={hover.xPct}
              y2="100"
              stroke="var(--oh-muted)"
              strokeWidth="1"
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
              className="pointer-events-none"
            />
          )}
        </svg>
        {hover &&
          series.map((row) => {
            const value = row.values[hover.index] ?? 0;
            const yPct = height - (value / maxValue) * height;
            return (
              <ChartHoverDot
                key={`hover-${row.id}`}
                xPct={hover.xPct}
                yPct={yPct}
                color={row.color}
              />
            );
          })}
      </div>
      <div className="absolute left-0 top-0 bottom-0 flex flex-col justify-between text-xs text-text-dim -ml-2">
        <span>{maxValue.toLocaleString()}</span>
        <span>{Math.round(maxValue / 2).toLocaleString()}</span>
        <span>0</span>
      </div>
      <div className="absolute bottom-0 left-0 right-0 mt-2 flex justify-between text-xs text-text-dim">
        {dates
          .filter((_, index) => index % dateStep === 0)
          .map((date) => (
            <span key={date}>{formatShortDate(date)}</span>
          ))}
      </div>
      {hover && (
        <ChartTooltip x={hover.clientX} y={hover.clientY}>
          <div className="text-xs font-medium text-foreground">
            {formatShortDate(dates[hover.index] ?? "")}
          </div>
          <div className="mt-1 flex flex-col gap-0.5">
            {series.map((row) => (
              <div
                key={row.id}
                className="flex items-center gap-2 text-xs tabular-nums text-muted"
              >
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: row.color }}
                />
                <span className="min-w-0 truncate">{row.label}</span>
                <span className="ml-auto text-foreground">
                  {(row.values[hover.index] ?? 0).toLocaleString()}
                </span>
              </div>
            ))}
          </div>
        </ChartTooltip>
      )}
    </div>
  );
}

export function AreaChart({
  data,
}: {
  data: { date: string; value: number }[];
}) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [hover, setHover] = React.useState<ChartHoverPoint | null>(null);
  const maxValue = Math.max(...data.map((d) => d.value), 1);
  const minValue = Math.min(...data.map((d) => d.value), 0);
  const range = maxValue - minValue || 1;

  const width = 100;
  const height = 100;
  const points = data.map((d, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((d.value - minValue) / range) * height;
    return `${x},${y}`;
  });

  const pathD = `M ${points.join(" L ")}`;
  const areaD = `${pathD} L ${width},${height} L 0,${height} Z`;

  if (data.length < 2) {
    return (
      <div className="flex h-full min-h-36 items-center justify-center text-sm text-muted">
        No usage data available yet.
      </div>
    );
  }

  const updateHover = (event: React.MouseEvent<HTMLDivElement>) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    const index = nearestPointIndex(event, data.length);
    setHover({
      index,
      xPct: (index / (data.length - 1)) * 100,
      clientX: event.clientX - rect.left,
      clientY: event.clientY - rect.top,
    });
  };

  const hoveredPoint = hover ? data[hover.index] : null;
  const hoveredY = hoveredPoint
    ? height - ((hoveredPoint.value - minValue) / range) * height
    : 0;

  return (
    <div
      ref={containerRef}
      className="relative h-full w-full pb-5"
      onMouseMove={updateHover}
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative h-full w-full">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="h-full w-full"
          preserveAspectRatio="none"
        >
          {[0, 25, 50, 75, 100].map((pct) => (
            <line
              key={pct}
              x1="0"
              y1={`${pct}%`}
              x2="100%"
              y2={`${pct}%`}
              stroke="var(--oh-border-subtle)"
              strokeWidth="0.5"
            />
          ))}
          <path d={areaD} fill="url(#usageAreaGradient)" opacity="0.3" />
          <path
            d={pathD}
            fill="none"
            stroke="var(--oh-color-primary)"
            strokeWidth="1.5"
            vectorEffect="non-scaling-stroke"
          />
          {hover && hoveredPoint && (
            <line
              x1={hover.xPct}
              y1="0"
              x2={hover.xPct}
              y2="100"
              stroke="var(--oh-muted)"
              strokeWidth="1"
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
              className="pointer-events-none"
            />
          )}
          <defs>
            <linearGradient
              id="usageAreaGradient"
              x1="0%"
              y1="0%"
              x2="0%"
              y2="100%"
            >
              <stop
                offset="0%"
                stopColor="var(--oh-color-primary)"
                stopOpacity="0.5"
              />
              <stop
                offset="100%"
                stopColor="var(--oh-color-primary)"
                stopOpacity="0"
              />
            </linearGradient>
          </defs>
        </svg>
        {hover && hoveredPoint && (
          <ChartHoverDot
            xPct={hover.xPct}
            yPct={hoveredY}
            color="var(--oh-color-primary)"
          />
        )}
      </div>
      <div className="absolute left-0 top-0 bottom-0 flex flex-col justify-between text-xs text-text-dim -ml-2">
        <span>{maxValue.toLocaleString()}</span>
        <span>{Math.round((maxValue + minValue) / 2).toLocaleString()}</span>
        <span>{minValue.toLocaleString()}</span>
      </div>
      <div className="absolute bottom-0 left-0 right-0 flex justify-between text-xs text-text-dim mt-2">
        {data
          .filter((_, i) => i % Math.ceil(data.length / 7) === 0)
          .map((d) => (
            <span key={d.date}>{formatShortDate(d.date)}</span>
          ))}
      </div>
      {hover && hoveredPoint && (
        <ChartTooltip x={hover.clientX} y={hover.clientY}>
          <div className="text-xs font-medium text-foreground">
            {formatShortDate(hoveredPoint.date)}
          </div>
          <div className="mt-0.5 text-xs tabular-nums text-muted">
            {hoveredPoint.value.toLocaleString()} conversations
          </div>
        </ChartTooltip>
      )}
    </div>
  );
}

export function PieChart({
  data,
  total,
}: {
  data: { value: number; color: string; label: string; percent: number }[];
  total: number;
}) {
  const size = 112;
  const strokeWidth = 12;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = React.useState<{
    label: string;
    value: number;
    percent: number;
    x: number;
    y: number;
  } | null>(null);
  let offset = 0;

  const updateHoverPosition = (
    event: React.MouseEvent<SVGCircleElement>,
    segment: { label: string; value: number; percent: number },
  ) => {
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) {
      return;
    }
    setHovered({
      label: segment.label,
      value: segment.value,
      percent: segment.percent,
      x: event.clientX - rect.left,
      y: event.clientY - rect.top,
    });
  };

  return (
    <div ref={containerRef} className="relative h-28 w-28">
      <svg viewBox={`0 0 ${size} ${size}`} className="h-full w-full">
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="transparent"
            stroke="var(--oh-border-subtle)"
            strokeWidth={strokeWidth}
            className="pointer-events-none"
          />
          {data.map((segment, index) => {
            const portion = total > 0 ? segment.value / total : 0;
            const segmentLength = circumference * portion;
            const dashArray = `${segmentLength} ${circumference - segmentLength}`;
            const segmentOffset = offset;
            offset += segmentLength;
            return (
              <circle
                key={`${segment.color}-${index}`}
                cx={size / 2}
                cy={size / 2}
                r={radius}
                fill="transparent"
                stroke={segment.color}
                strokeWidth={strokeWidth}
                strokeDasharray={dashArray}
                strokeDashoffset={-segmentOffset}
                strokeLinecap="round"
                className="cursor-pointer transition-[stroke-width] hover:stroke-[14]"
                onMouseEnter={(event) => updateHoverPosition(event, segment)}
                onMouseMove={(event) => updateHoverPosition(event, segment)}
                onMouseLeave={() => setHovered(null)}
              />
            );
          })}
        </g>
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 min-w-32 -translate-x-1/2 -translate-y-[calc(100%+8px)] rounded-lg border border-border-subtle bg-base-secondary px-3 py-2 shadow-lg"
          style={{ left: hovered.x, top: hovered.y }}
        >
          <div className="text-sm font-medium text-foreground">
            {hovered.label}
          </div>
          <div className="mt-0.5 text-xs tabular-nums text-muted">
            {formatCost(hovered.value)} · {hovered.percent.toFixed(1)}%
          </div>
        </div>
      )}
    </div>
  );
}
