CC=clang
OUTPUT=com.albert-mueller.opencore-patcher-t2.privileged-helper

# Optional, release builds only:
#   make CERT_SHA1=<40 hex chars>        also hard-code the expected leaf certificate
#   make CLIENT_ID=<bundle identifier>   if the app's bundle identifier was changed
PIN_FLAGS=
ifneq ($(CERT_SHA1),)
PIN_FLAGS+= -DOCLP_PINNED_LEAF_SHA1=\"$(CERT_SHA1)\"
endif
ifneq ($(CLIENT_ID),)
PIN_FLAGS+= -DOCLP_CLIENT_IDENTIFIER=\"$(CLIENT_ID)\"
endif

all: clean release

release: main.m
	$(CC) -framework Foundation -framework Security -arch x86_64 -arch arm64 -mmacosx-version-min=10.9 $(PIN_FLAGS) -o $(OUTPUT) main.m

debug: main.m
	$(CC) -framework Foundation -framework Security -arch x86_64 -arch arm64 -mmacosx-version-min=10.9 -o $(OUTPUT) main.m -DDEBUG

clean:
	/bin/rm -f $(OUTPUT)
