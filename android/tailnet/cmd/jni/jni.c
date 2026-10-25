#include <jni.h>
#include <stdlib.h>

extern void *pa_tailnet_call(char *data, int length, int *output_length);

JNIEXPORT jbyteArray JNICALL
Java_org_personalassistant_app_TailnetNative_callNative(JNIEnv *env, jobject self, jbyteArray input) {
    (void)self;
    jsize length = (*env)->GetArrayLength(env, input);
    if (length > 48 * 1024 * 1024) return NULL;
    char *data = malloc((size_t)length + 1);
    if (!data) return NULL;
    (*env)->GetByteArrayRegion(env, input, 0, length, (jbyte *)data);
    if ((*env)->ExceptionCheck(env)) { free(data); return NULL; }
    int output_length = 0;
    void *output = pa_tailnet_call(data, length, &output_length);
    free(data);
    jbyteArray result = (*env)->NewByteArray(env, output_length);
    if (result) (*env)->SetByteArrayRegion(env, result, 0, output_length, output);
    free(output);
    return result;
}
