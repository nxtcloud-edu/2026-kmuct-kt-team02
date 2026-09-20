import { Link } from "react-router-dom";
import { Logo } from "@/components/brand/Logo";
import { COPY } from "@/lib/labels";
import { useApp } from "@/providers/AppProvider";

const OFFICIAL_LINKS = [
  { label: "온통청년", href: "https://www.youthcenter.go.kr" },
  { label: "서울시 청년몽땅정보통", href: "https://youth.seoul.go.kr" },
  { label: "복지로", href: "https://www.bokjiro.go.kr" },
  { label: "고용24", href: "https://www.work24.go.kr" },
];

const SERVICE_LINKS = [
  { to: "/chat", label: "AI 혜택 상담" },
  { to: "/policies", label: "정책 모아보기" },
  { to: "/mypage", label: "마이페이지" },
];

export function AppFooter() {
  const { mockMode } = useApp();

  return (
    <footer className="mt-14 border-t border-line bg-white">
      <div className="mx-auto grid max-w-[1400px] gap-8 px-4 py-10 sm:px-6 lg:grid-cols-[1.4fr_1fr_1fr] lg:px-8">
        <div>
          <Logo size={30} />
          <p className="mt-3 max-w-md text-[0.9375rem] leading-relaxed text-ink-600">
            쏘다는 공식 공고를 근거로 청년 지원제도를 찾아 주는 대화형 서비스입니다.
            모든 판정에 공고 원문 각주와 최종 확인일을 함께 보여 줍니다.
          </p>
          {mockMode && (
            <p className="mt-3 inline-flex rounded-lg border border-check-border bg-check-bg px-2.5 py-1.5 text-[0.875rem] font-semibold text-check">
              목업 데이터 모드 · 서버 연결 전
            </p>
          )}
        </div>

        <nav aria-label="서비스 메뉴">
          <h2 className="text-[0.9375rem] font-bold text-ink-900">서비스</h2>
          <ul className="mt-3 space-y-2">
            {SERVICE_LINKS.map((link) => (
              <li key={link.to}>
                <Link
                  to={link.to}
                  className="rounded text-[0.9375rem] text-ink-600 transition-colors hover:text-brand-700 focus-ring"
                >
                  {link.label}
                </Link>
              </li>
            ))}
          </ul>
        </nav>

        <nav aria-label="공식 기관 바로가기">
          <h2 className="text-[0.9375rem] font-bold text-ink-900">공식 기관</h2>
          <ul className="mt-3 space-y-2">
            {OFFICIAL_LINKS.map((link) => (
              <li key={link.href}>
                <a
                  href={link.href}
                  target="_blank"
                  rel="noreferrer noopener"
                  className="rounded text-[0.9375rem] text-ink-600 transition-colors hover:text-brand-700 focus-ring"
                >
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </div>

      <div className="border-t border-line-soft px-4 py-5 sm:px-6 lg:px-8">
        <p className="mx-auto max-w-[1400px] text-[0.875rem] leading-relaxed text-ink-500">
          쏘다의 판정은 참고용 안내이며 최종 지원 대상 여부는 담당 기관 심사로 결정됩니다.
          {" "}
          {COPY.finalCheck}.
        </p>
      </div>
    </footer>
  );
}
