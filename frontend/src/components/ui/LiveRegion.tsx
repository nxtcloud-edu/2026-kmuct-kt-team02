/**
 * 라이브 리전.
 *
 * 갱신이 일어나기 전에 페이지에 있어야 한다 (docs/08-risks-privacy-a11y.md 3장).
 * 답변 본문은 여기에 넣지 않는다. 진행 단계와 완료만 짧게 알린다.
 * 오류만 단호한(assertive) 영역을 쓴다.
 */
export function LiveRegion({
  politeMessage,
  assertiveMessage,
}: {
  politeMessage: string;
  assertiveMessage: string;
}) {
  return (
    <>
      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {politeMessage}
      </p>
      <p role="alert" aria-live="assertive" aria-atomic="true" className="sr-only">
        {assertiveMessage}
      </p>
    </>
  );
}
