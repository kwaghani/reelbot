#import <React/RCTBridgeModule.h>
@interface RCT_EXTERN_MODULE(ReelBotQueue, NSObject)
RCT_EXTERN_METHOD(enqueue:(NSString *)url timestamp:(double)timestamp resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(read:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
RCT_EXTERN_METHOD(acknowledge:(NSString *)identifier resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject)
@end
