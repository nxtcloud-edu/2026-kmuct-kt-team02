import { useEffect, useState } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { LogOut, Menu, X } from "lucide-react";
import { Logo } from "@/components/brand/Logo";
import { Button } from "@/components/ui/Button";
import { cn } from "@/lib/cn";
import { useApp } from "@/providers/AppProvider";

const NAV_ITEMS = [
  { to: "/", label: "홈" },
  { to: "/chat", label: "AI 혜택 상담" },
  { to: "/policies", label: "정책 모아보기" },
  { to: "/mypage", label: "마이페이지" },
] as const;

export function AppHeader() {
  const location = useLocation();
  const { auth, signOut, ready } = useApp();
  const [menuOpen, setMenuOpen] = useState(false);

  useEffect(() => setMenuOpen(false), [location.pathname]);

  return (
    <header className="sticky top-0 z-40 border-b border-line bg-white/95 backdrop-blur-xl">
      {/* 헤더와 본문을 분명히 나누는 그라데이션 라인 */}
      <div aria-hidden="true" className="h-1 w-full bg-brand-gradient" />

      <div className="mx-auto flex h-16 max-w-[1400px] items-center justify-between gap-4 px-4 sm:px-6 lg:px-8">
        <Link to="/" className="rounded-xl focus-ring" aria-label="쏘다 홈으로 이동">
          <Logo size={32} />
        </Link>

        <nav aria-label="주요 메뉴" className="hidden lg:block">
          <ul className="flex items-center gap-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    cn(
                      "block rounded-xl px-4 py-2 text-[0.9375rem] font-semibold transition-colors focus-ring",
                      isActive
                        ? "bg-brand-50 text-brand-700"
                        : "text-ink-600 hover:bg-canvas-200 hover:text-brand-700",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="hidden items-center gap-2 lg:flex">
          {ready && auth ? (
            <>
              <Link
                to="/mypage"
                className="flex items-center gap-2 rounded-xl border border-line bg-white px-3 py-1.5 text-[0.875rem] font-semibold text-ink-700 transition-colors hover:border-line-strong hover:text-brand-700 focus-ring"
              >
                <span
                  aria-hidden="true"
                  className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-gradient text-[0.8125rem] font-bold text-white"
                >
                  {auth.email.slice(0, 1).toUpperCase()}
                </span>
                {auth.email.split("@")[0]}
              </Link>
              <Button
                variant="secondary"
                size="sm"
                onClick={signOut}
                leftIcon={<LogOut aria-hidden="true" className="h-3.5 w-3.5" />}
              >
                로그아웃
              </Button>
            </>
          ) : (
            <Link to="/login" className="rounded-xl focus-ring">
              <Button size="sm">로그인</Button>
            </Link>
          )}
        </div>

        <button
          type="button"
          onClick={() => setMenuOpen((open) => !open)}
          aria-expanded={menuOpen}
          aria-controls="mobile-menu"
          aria-label={menuOpen ? "메뉴 닫기" : "메뉴 열기"}
          className="flex h-11 w-11 items-center justify-center rounded-xl border border-line bg-white text-ink-700 transition-colors hover:border-line-strong hover:text-brand-700 focus-ring lg:hidden"
        >
          {menuOpen ? (
            <X aria-hidden="true" className="h-5 w-5" />
          ) : (
            <Menu aria-hidden="true" className="h-5 w-5" />
          )}
        </button>
      </div>

      {menuOpen && (
        <div
          id="mobile-menu"
          className="animate-fade-in border-t border-line bg-white px-4 py-4 lg:hidden"
        >
          <nav aria-label="모바일 메뉴">
            <ul className="space-y-1">
              {NAV_ITEMS.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) =>
                      cn(
                        "block rounded-xl px-4 py-3 text-[0.9375rem] font-semibold transition-colors focus-ring",
                        isActive
                          ? "bg-brand-50 text-brand-700"
                          : "text-ink-700 hover:bg-canvas-200",
                      )
                    }
                  >
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>

          <div className="mt-3 border-t border-line-soft pt-3">
            {ready && auth ? (
              <div className="flex items-center justify-between gap-3">
                <span className="text-[0.9375rem] font-semibold text-ink-700">
                  {auth.email}
                </span>
                <Button variant="secondary" size="sm" onClick={signOut}>
                  로그아웃
                </Button>
              </div>
            ) : (
              <Link to="/login" className="block rounded-xl focus-ring">
                <Button block>로그인</Button>
              </Link>
            )}
          </div>
        </div>
      )}
    </header>
  );
}
