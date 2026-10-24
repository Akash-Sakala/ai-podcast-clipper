import { ThemeMenu } from "./theme-menu";

const NavHeader = () => {
  return (
    <header className="bg-background/85 sticky top-0 z-20 border-b backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <div className="font-display flex items-center gap-2 text-lg font-semibold tracking-tight">
          <span
            aria-hidden
            className="bg-tally inline-block size-2.5 rounded-full"
          />
          podcast clipper
        </div>
        <ThemeMenu />
      </div>
    </header>
  );
};

export default NavHeader;
