import { cn } from "@/lib/cn";

interface LogoProps {
  variant?: "mark" | "full";
  /** 심볼 한 변 크기(px) */
  size?: number;
  className?: string;
  inverted?: boolean;
}

/** 쏘다 로고. 그라데이션 배지 안에 두 사람과 스파클을 넣었다. */
export function Logo({
  variant = "full",
  size = 32,
  className,
  inverted = false,
}: LogoProps) {
  return (
    <span className={cn("inline-flex items-center gap-2.5", className)}>
      <svg
        viewBox="0 0 48 48"
        width={size}
        height={size}
        role="img"
        aria-label="쏘다"
        className="shrink-0"
      >
        <defs>
          <linearGradient id="sodda-badge" x1="4" y1="44" x2="44" y2="4">
            <stop offset="0%" stopColor="#7C80F0" />
            <stop offset="52%" stopColor="#5B7BF5" />
            <stop offset="100%" stopColor="#52C6E8" />
          </linearGradient>
        </defs>

        <rect x="2" y="2" width="44" height="44" rx="13" fill="url(#sodda-badge)" />
        <rect
          x="2.75"
          y="2.75"
          width="42.5"
          height="42.5"
          rx="12.25"
          fill="none"
          stroke="rgba(255,255,255,0.4)"
          strokeWidth="1.5"
        />

        <g fill="#FFFFFF">
          <circle cx="19" cy="19.5" r="4" />
          <circle cx="29" cy="19.5" r="4" />
          <path d="M10.5 38c0-7.2 2.7-11.8 5.9-11.8 2.6 0 4.9 3.1 7.6 8 2.7-4.9 5-8 7.6-8 3.2 0 5.9 4.6 5.9 11.8Z" />
        </g>

        <path
          d="M37.5 8.2c.7 3.1 1.5 3.9 4.6 4.6-3.1.7-3.9 1.5-4.6 4.6-.7-3.1-1.5-3.9-4.6-4.6 3.1-.7 3.9-1.5 4.6-4.6Z"
          fill="rgba(255,255,255,0.92)"
        />
      </svg>

      {variant === "full" && (
        <span
          className={cn(
            "font-extrabold leading-none tracking-tight",
            inverted ? "text-white" : "text-ink-900",
          )}
          style={{ fontSize: size * 0.58 }}
        >
          쏘다
        </span>
      )}
    </span>
  );
}
