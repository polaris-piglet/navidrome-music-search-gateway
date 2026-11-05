// JSON subprocess adapter: same pinned music-lib dependency as music-dl.
package main

import (
	"encoding/json"
	"fmt"
	"github.com/guohuiyuan/music-lib/kugou"
	"github.com/guohuiyuan/music-lib/kuwo"
	"github.com/guohuiyuan/music-lib/model"
	"github.com/guohuiyuan/music-lib/netease"
	"github.com/guohuiyuan/music-lib/qq"
	"os"
	"strings"
)

func main() {
	var in struct {
		Op     string
		Source string
		Query  string
		Song   model.Song
	}
	if json.NewDecoder(os.Stdin).Decode(&in) != nil {
		os.Exit(2)
	}
	var result interface{}
	var err error
	switch in.Op {
	case "search":
		switch in.Source {
		case "netease":
			result, err = netease.Search(in.Query)
		case "qq":
			result, err = qq.Search(in.Query)
		case "kugou":
			result, err = kugou.Search(in.Query)
		case "kuwo":
			result, err = kuwo.Search(in.Query)
		default:
			err = fmt.Errorf("unsupported source")
		}
	case "resolve":
		// Optional future authentication: explicit local secret file only; never output it.
		cookie := ""
		if path := os.Getenv("MUSIC_PROVIDER_AUTH_FILE"); path != "" {
			raw, e := os.ReadFile(path)
			var credentials map[string]string
			if e != nil || json.Unmarshal(raw, &credentials) != nil {
				os.Exit(2)
			}
			cookie = strings.TrimSpace(credentials[in.Song.Source])
		}
		in.Song.URL = ""
		var u string
		switch in.Song.Source {
		case "netease":
			u, err = netease.New(cookie).GetDownloadURL(&in.Song)
		case "qq":
			u, err = qq.New(cookie).GetDownloadURL(&in.Song)
		case "kugou":
			u, err = kugou.New(cookie).GetDownloadURL(&in.Song)
		case "kuwo":
			u, err = kuwo.New(cookie).GetDownloadURL(&in.Song)
		default:
			err = fmt.Errorf("unsupported source")
		}
		result = map[string]interface{}{"url": u, "song": in.Song}
	default:
		err = fmt.Errorf("unsupported operation")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "provider request failed")
		os.Exit(1)
	}
	json.NewEncoder(os.Stdout).Encode(result)
}
