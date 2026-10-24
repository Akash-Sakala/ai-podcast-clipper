import Link from "next/link";
import { LogoMark } from "./logo";
import { ThemeMenu } from "./theme-menu";

const NavHeader = () => {
  return (
    <header className="bg-background/85 sticky top-0 z-20 border-b backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link
          href="/"
          className="font-display -ml-1 flex items-center gap-2 rounded-md px-1 text-lg font-semibold tracking-tight"
        >
          <LogoMark className="text-muted-foreground size-7" />
          podcast clipper
        </Link>
        <ThemeMenu />
      </div>
    </header>
  );
};

export default NavHeader;
