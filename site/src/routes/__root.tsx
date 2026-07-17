import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRoute,
} from '@tanstack/react-router';
import type { ReactNode } from 'react';
import appCss from '../styles.css?url';
import { categories } from '../data';

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1' },
      { title: 'acode 컨벤션 룰' },
      {
        name: 'description',
        content: 'acode 결정적 룰 엔진의 공식 컨벤션 문서 — 어떤 코드를 넣으면 무엇을 제안하는지.',
      },
    ],
    links: [{ rel: 'stylesheet', href: appCss }],
  }),
  component: RootComponent,
  notFoundComponent: NotFound,
});

function RootComponent() {
  return (
    <RootDocument>
      <Outlet />
    </RootDocument>
  );
}

function NotFound() {
  return (
    <div className="container">
      <h1>404</h1>
      <p>
        페이지를 찾을 수 없습니다. <Link to="/">카테고리 목록으로</Link>
      </p>
    </div>
  );
}

function RootDocument({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <head>
        <HeadContent />
      </head>
      <body>
        <header className="site-header">
          <div className="site-header-inner">
            <Link to="/" className="site-title">
              acode rules
            </Link>
            <nav className="site-nav">
              {categories.map((c) => (
                <Link
                  key={c.slug}
                  to="/category/$slug"
                  params={{ slug: c.slug }}
                  activeProps={{ className: 'active' }}
                >
                  {c.slug}
                </Link>
              ))}
            </nav>
          </div>
        </header>
        {children}
        <footer className="site-footer">
          conventions/*.json에서 빌드 시점에 자동 생성 — 룰이 추가되면 이
          페이지도 함께 갱신됩니다. 엔진 출력은 실제 RuleEngine 실행 결과입니다.
        </footer>
        <Scripts />
      </body>
    </html>
  );
}
