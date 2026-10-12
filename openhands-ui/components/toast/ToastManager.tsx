import { type PropsWithChildren } from "react";
import { Toaster, type ToasterProps } from "sonner";

export const ToastManager = ({
  children,
  position = "top-right",
  ...props
}: PropsWithChildren<ToasterProps>) => {
  return (
    <>
      <Toaster position={position} {...props} />
      {children}
    </>
  );
};
