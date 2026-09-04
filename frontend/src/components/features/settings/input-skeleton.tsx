export function InputSkeleton() {
  return (
    <div className="flex flex-col gap-2.5">
      <div className="h-[20px] w-[70px] skeleton" />
      <div className="h-[40px] w-full min-w-0 skeleton" />
    </div>
  );
}
