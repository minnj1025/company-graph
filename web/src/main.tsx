import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { startTexts } from "./texts";

// 분석 페이지(?page=주소)는 그래프 없이 연다. 그래프 화면의 코드는 받지 않는다
const App = lazy(() => import("./App").then((module) => ({ default: module.App })));
const PageView = lazy(() => import("./PageView"));
const page = new URLSearchParams(window.location.search).get("page");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Suspense fallback={null}>{page ? <PageView slug={page} /> : <App />}</Suspense>
  </StrictMode>,
);

// 운영자가 고쳐 쓴 글을 입힌다. 주소 끝에 ?edit 를 붙여 열면 화면에서 바로 고칠 수 있다
void startTexts();
