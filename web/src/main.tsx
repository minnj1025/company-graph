import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { startTexts } from "./texts";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// 운영자가 고쳐 쓴 글을 입힌다. 주소 끝에 ?edit 를 붙여 열면 화면에서 바로 고칠 수 있다
void startTexts();
