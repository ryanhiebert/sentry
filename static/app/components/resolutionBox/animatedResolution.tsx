import {ReactNode, useEffect, useRef, useState} from 'react';
import styled from '@emotion/styled';
import {useResizeObserver} from '@react-aria/utils';
import {motion, useAnimation, Variants} from 'framer-motion';

import {ErrorGemlin} from 'sentry/components/resolutionBox/errorGremlin';
import {IconCheckmark} from 'sentry/icons';
import {space} from 'sentry/styles/space';

type AnimatedResolutionProps = {animate: boolean; children: ReactNode};

const portalVariants: Variants = {
  open: {
    scale: 1,
    transition: {
      ease: 'easeOut',
      delay: 0.1,
    },
  },
  closed: {
    scale: 0,
    transition: {
      ease: 'easeOut',
      delay: 0.3,
    },
  },
};

const checkVariants: Variants = {
  hidden: {
    y: 30,
    rotate: -720,
  },
  shown: {
    y: [30, -30, 0],
    rotate: [-720, -360, 0],
    transition: {
      ease: 'easeInOut',
    },
  },
};

const textVariants: Variants = {
  hidden: {
    opacity: 0,
  },
  shown: {
    opacity: 1,
  },
};

export function AnimatedResolution({animate, children}: AnimatedResolutionProps) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [bannerWidth, setBannerWidth] = useState(0);
  useResizeObserver<HTMLDivElement>({
    ref,
    onResize: () => {
      if (ref.current) {
        setBannerWidth(ref.current.clientWidth);
      }
    },
  });

  const [shouldAnimate, setShouldAnimate] = useState(animate ?? false);
  useEffect(() => {
    if (animate) {
      setShouldAnimate(true);
    }
  }, [animate]);

  const portalControls = useAnimation();
  const checkControls = useAnimation();
  const textControls = useAnimation();

  const onEndRun = async () => {
    await portalControls.start('open');
  };

  const onEndJump = async () => {
    portalControls.start('closed');
    await checkControls.start('shown');
    textControls.start('shown');
  };

  return (
    <div style={{width: '100%'}} ref={ref}>
      <LeftContainer>
        <Portal animate={portalControls} variants={portalVariants} initial="closed" />
        {shouldAnimate && (
          <ErrorGemlin
            onEndRun={onEndRun}
            onEndJump={onEndJump}
            runFromPosition={`${bannerWidth + 250}px`}
          />
        )}
        <ClipPath>
          <CheckContainer
            {...(shouldAnimate
              ? {
                  animate: checkControls,
                  variants: checkVariants,
                  initial: 'hidden',
                }
              : {})}
          >
            <StyledIconCheckmark color="successText" />
          </CheckContainer>
        </ClipPath>
      </LeftContainer>
      <StyledText
        animate={shouldAnimate ? textControls : undefined}
        variants={textVariants}
        initial={shouldAnimate ? 'hidden' : 'shown'}
      >
        {children}
      </StyledText>
    </div>
  );
}

const LeftContainer = styled('div')`
  position: absolute;
  bottom: 4px;
  left: ${space(2)};
  width: 42px;
  height: calc(100% - 8px);
  display: flex;
  align-items: center;
  justify-content: center;
`;

const ClipPath = styled('div')`
  position: absolute;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  clip-path: inset(-20px 0 3px 0);
`;

const CheckContainer = styled(motion.div)`
  left: 0;
  top: 0;
  height: ${p => p.theme.iconSizes.sm};
  width: ${p => p.theme.iconSizes.sm};
`;

const StyledIconCheckmark = styled(IconCheckmark)`
  /* override margin defined in BannerSummary */
  margin-top: 0 !important;
`;

const StyledText = styled(motion.span)`
  padding-left: 35px;
`;

const Portal = styled(motion.div)`
  position: absolute;
  bottom: 0;
  left: 0;
  display: block;
  width: 100%;
  height: 10px;
  background: ${p => p.theme.gray300};
  box-shadow: inset 0 3px 0 0 ${p => p.theme.gray200};
  border-radius: 120px / 30px;
`;
