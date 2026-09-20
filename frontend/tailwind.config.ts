import type { Config } from "tailwindcss";

/**
 * 색 규칙은 frontend/README.md 6장과 docs/08-risks-privacy-a11y.md 3장을 따른다.
 * likely 초록, check 주황, unlikely 회색. 빨강은 마감 임박에만 쓴다.
 * 상태는 색만으로 구분하지 않고 문구와 아이콘을 함께 쓴다.
 */
const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Pretendard Variable",
          "Pretendard",
          "Noto Sans KR",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "sans-serif",
        ],
      },
      colors: {
        canvas: {
          50: "#FBFBFF",
          100: "#F5F6FD",
          200: "#EDEFFA",
          300: "#E3E6F6",
        },
        /** 본문 대비 4.5:1 이상을 만족하는 잉크 계열 */
        ink: {
          900: "#141A33",
          800: "#1F2547",
          700: "#323A61",
          600: "#4B5480",
          500: "#5F688F",
          400: "#8A92B0",
        },
        /** 유리·흰 배경 위에서도 보이는 경계선 */
        line: {
          soft: "#E6E9F7",
          DEFAULT: "#D5D9EF",
          strong: "#B7BFDE",
        },
        brand: {
          50: "#EEF0FE",
          100: "#DFE2FD",
          200: "#C3C8FB",
          300: "#A0A7F7",
          400: "#7C80F0",
          500: "#5D61E6",
          600: "#4A4AD0",
          700: "#3A37A6",
        },
        aurora: {
          violet: "#8B7BF2",
          blue: "#4A7DFF",
          sky: "#5EC8F0",
        },
        /** 판정 상태 */
        likely: { DEFAULT: "#0F7A57", bg: "#E6F7EF", border: "#9DDCC2" },
        check: { DEFAULT: "#8A5A0B", bg: "#FDF3E0", border: "#EBCF99" },
        unlikely: { DEFAULT: "#4B5480", bg: "#EEF0F7", border: "#C8CEE2" },
        /** 마감 임박 전용 */
        urgent: { DEFAULT: "#B3283C", bg: "#FCEBEE", border: "#F0BAC3" },
      },
      borderRadius: { "4xl": "1.75rem", "5xl": "2.25rem" },
      boxShadow: {
        card: "0 1px 2px rgba(31,37,71,0.04), 0 10px 24px -14px rgba(31,37,71,0.18)",
        "card-hover":
          "0 1px 2px rgba(31,37,71,0.05), 0 18px 38px -16px rgba(31,37,71,0.26)",
        panel: "0 1px 2px rgba(31,37,71,0.04), 0 24px 56px -22px rgba(31,37,71,0.24)",
        brand: "0 10px 24px -10px rgba(93,97,230,0.5)",
        /**
         * 위로 떠오른 느낌.
         * 상단 안쪽 흰 선으로 모서리를 살리고, 그림자를 세 겹으로 겹쳐 거리감을 만든다.
         */
        lift: [
          "inset 0 1px 0 0 rgba(255,255,255,0.9)",
          "0 1px 2px rgba(31,37,71,0.05)",
          "0 6px 14px -6px rgba(31,37,71,0.12)",
          "0 20px 36px -14px rgba(31,37,71,0.20)",
          "0 40px 64px -28px rgba(31,37,71,0.22)",
        ].join(", "),
        "lift-hover": [
          "inset 0 1px 0 0 rgba(255,255,255,0.9)",
          "0 1px 2px rgba(31,37,71,0.05)",
          "0 10px 20px -8px rgba(31,37,71,0.14)",
          "0 26px 44px -16px rgba(31,37,71,0.24)",
          "0 52px 80px -32px rgba(31,37,71,0.26)",
        ].join(", "),
      },
      backgroundImage: {
        "brand-gradient": "linear-gradient(135deg, #7C80F0 0%, #5B7BF5 52%, #52C6E8 100%)",
        "hero-gradient": "linear-gradient(150deg, #8B7BF2 0%, #5B7BF5 52%, #4EC3E8 100%)",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": { "0%": { opacity: "0" }, "100%": { opacity: "1" } },
        "slide-in-right": {
          "0%": { opacity: "0", transform: "translateX(24px)" },
          "100%": { opacity: "1", transform: "translateX(0)" },
        },
        "slide-up-sheet": {
          "0%": { transform: "translateY(100%)" },
          "100%": { transform: "translateY(0)" },
        },
        float: {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(0,-18px,0) scale(1.04)" },
        },
        "float-slow": {
          "0%, 100%": { transform: "translate3d(0,0,0) scale(1)" },
          "50%": { transform: "translate3d(14px,16px,0) scale(1.05)" },
        },
        shimmer: { "100%": { transform: "translateX(100%)" } },
        caret: { "0%, 100%": { opacity: "1" }, "50%": { opacity: "0" } },
        /** 상태 배지가 바뀔 때 0.3초 동안 부드럽게 (README 3-5) */
        "badge-swap": {
          "0%": { opacity: "0.35", transform: "scale(0.96)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.3s ease-out both",
        "fade-in": "fade-in 0.22s ease-out both",
        "slide-in-right": "slide-in-right 0.26s ease-out both",
        "slide-up-sheet": "slide-up-sheet 0.28s ease-out both",
        float: "float 15s ease-in-out infinite",
        "float-slow": "float-slow 20s ease-in-out infinite",
        shimmer: "shimmer 1.6s infinite",
        caret: "caret 1s step-end infinite",
        "badge-swap": "badge-swap 0.3s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
