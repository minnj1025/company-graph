/** 데이터 출처. 모든 화면의 맨 아래에 둔다. */
export function Credit() {
  return (
    <footer className="credit">
      <b>데이터 출처</b> 금융감독원 전자공시시스템(
      <a href="https://dart.fss.or.kr" target="_blank" rel="noreferrer">
        DART
      </a>
      )과{" "}
      <a href="https://opendart.fss.or.kr" target="_blank" rel="noreferrer">
        OpenDART
      </a>{" "}
      API의 공시 원문 · 공정거래위원회의 대규모기업집단 소속회사 현황(
      <a href="https://www.data.go.kr" target="_blank" rel="noreferrer">
        공공데이터포털
      </a>
      ). 공시에 적힌 사실을 정리한 것이며 투자 권유가 아닙니다.
    </footer>
  );
}
