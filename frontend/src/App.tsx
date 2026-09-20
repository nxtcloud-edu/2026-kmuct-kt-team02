import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { AppProvider } from "@/providers/AppProvider";
import { AppHeader } from "@/components/layout/AppHeader";
import { AppFooter } from "@/components/layout/AppFooter";
import { ScrollToTop } from "@/components/layout/ScrollToTop";
import { PageBackground } from "@/components/ui/Surface";
import { ToastViewport } from "@/components/ui/Toast";
import { ChatPage } from "@/pages/ChatPage";
import { PoliciesPage } from "@/pages/PoliciesPage";
import { LoginPage } from "@/pages/LoginPage";
import { MyPage } from "@/pages/MyPage";

export default function App() {
  return (
    // S3 정적 호스팅에서 새로고침과 뒤로가기가 그대로 동작하도록 HashRouter를 쓴다.
    <HashRouter>
      <AppProvider>
        <PageBackground />
        <ScrollToTop />

        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[70] focus:rounded-xl focus:bg-white focus:px-5 focus:py-3 focus:text-[0.9375rem] focus:font-semibold focus:text-brand-700 focus:shadow-panel"
        >
          본문으로 바로가기
        </a>

        <div className="flex min-h-screen flex-col">
          <AppHeader />
          <main id="main" className="flex-1">
            <Routes>
              {/* 홈이 AI 대화 화면이다 */}
              <Route path="/" element={<ChatPage />} />
              <Route path="/chat" element={<Navigate to="/" replace />} />
              <Route path="/policies" element={<PoliciesPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route path="/mypage" element={<MyPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </main>
          <AppFooter />
        </div>

        <ToastViewport />
      </AppProvider>
    </HashRouter>
  );
}
