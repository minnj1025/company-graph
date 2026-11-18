/** 답의 글을 그릴 때 쓰는 것: 접수번호 칩, 기업 링크, 비율 막대 칸. 채팅(Chat.tsx)과 분석 페이지(PageView.tsx)가 함께 쓴다 */
import { Children, type ReactNode } from "react";

export const DART = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=";

/** 답에 나온 14자리 접수번호를 DART 원문 링크로 바꾼다. 글에는 14자리 대신 접수일을 담은 작은 칩("공시 08-14")을 보이고,
 * 번호는 마우스를 올리면 보이게 둔다. 답 글 자체는 그대로라 근거 검사·채점에는 영향이 없다. 앞의 "접수번호" 낱말은 칩과 겹쳐 뗀다 */
export const linkReceipts = (text: string) =>
  text.replace(/(?:접수번호\s*:?\s*)?(?<![\d/=])(20\d{2})(\d{2})(\d{2})(\d{6})(?!\d)/g, (_all, year, month, day, rest) =>
    `[공시 ${month}-${day}](${DART}${year}${month}${day}${rest} "${year}${month}${day}${rest}")`);
const escapeRegExp = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
/** 답에 나온 기업 이름을 그래프의 그 점으로 가는 링크로 바꾼다. 이미 링크인 곳은 건드리지 않는다 */
export function linkCompanies(text: string, companies: { id: number; name: string }[]): string {
  const ids = new Map<string, number>();
  for (const company of companies) if (company.name.length >= 2 && !ids.has(company.name)) ids.set(company.name, company.id);
  if (ids.size === 0) return text;
  // 긴 이름부터 맞춰야 "SK"가 "SK하이닉스" 안에서 먼저 잡히지 않는다. 영문·숫자로 이어지는 낱말의 일부는 건너뛴다
  const names = [...ids.keys()].sort((a, b) => b.length - a.length).map(escapeRegExp).join("|");
  const pattern = new RegExp(`(\\[[^\\]]*\\]\\([^)]*\\)|https?:\\S+)|(?<![A-Za-z0-9])(${names})(?![A-Za-z0-9])`, "g");
  return text.replace(pattern, (all, link, name) => (link ? all : `[${name}](#company-${ids.get(name)})`));
}

const plain = (children: ReactNode): string =>
  Children.toArray(children)
    .map((child) => (typeof child === "string" || typeof child === "number" ? String(child) : ""))
    .join("");

/** 표의 칸. "45.2%"처럼 비율만 적힌 칸은 숫자 뒤에 그 크기만큼의 막대를 깐다 */
export function Cell({ children }: { children?: ReactNode }) {
  const match = /^\s*(\d{1,3}(?:\.\d+)?)\s*%\s*$/.exec(plain(children));
  const value = match ? Number(match[1]) : null;
  if (value === null || value > 100) return <td>{children}</td>;
  return (
    <td className="pct">
      <i style={{ width: `${Math.max(value, 1.5)}%` }} />
      <span>{children}</span>
    </td>
  );
}

