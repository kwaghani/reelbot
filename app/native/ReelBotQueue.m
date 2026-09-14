#import <React/RCTBridgeModule.h>
@interface RCT_EXTERN_MODULE(ReelBotQueue, NSObject)
RCT_EXTERN_METHOD(enqueue:(NSString *)url timestamp:(double)timestamp resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(read:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(acknowledge:(NSString *)identifier resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(quarantine:(NSString *)identifier reason:(NSString *)reason resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(recordDrain:(NSDictionary *)report resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(canary:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(diagnostics:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(beginMotionProfile:(NSString *)label duration:(double)duration)
RCT_EXTERN_METHOD(mapThumbnail:(NSString *)place latitude:(double)latitude longitude:(double)longitude color:(NSString *)color resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
@end
